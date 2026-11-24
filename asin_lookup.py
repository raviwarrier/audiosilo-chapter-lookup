"""
asin_lookup.py - Dev tool: find ASINs via Audible catalogue search

What it does:
  Reads unprocessed_titles.txt (format: "Title | Author | status").
  For books with status "no_asin", searches Audible catalogue API
  by title+author to find ASINs. When an ASIN is found, looks up
  chapters from Audnexus, writes chapters.json sidecar, and triggers
  AudioSilo rescan.

Depends on:
  - requests
  - chapter_sidecar.audiosilo_client.AudioSiloClient
  - chapter_sidecar.config.load_config
  - chapter_sidecar.chapters.Chapter, validate_chapters
  - Audible catalogue API: https://api.audible.com/1.0/catalog/products
  - Audnexus API: https://api.audnex.us/books/{asin}/chapters
  - AudioSilo server at AUDIOSILO_BASE_URL

Output:
  Prints each book with found ASINs and chapter lookup results.
  Updates unprocessed_titles.txt in place.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from chapter_sidecar.audiosilo_client import AudioSiloClient
from chapter_sidecar.chapters import Chapter, validate_chapters
from chapter_sidecar.config import load_config

INPUT_FILE = Path("unprocessed_titles.txt")
AUDIBLE_URL = "https://api.audible.com/1.0/catalog/products"
AUDNEXUS_URL = "https://api.audnex.us/books/{asin}/chapters"
REQUEST_DELAY = 1.0


def load_unprocessed() -> list[tuple[str, str, str]]:
    if not INPUT_FILE.exists():
        print(f"ERROR: {INPUT_FILE} not found.", file=sys.stderr)
        return []
    lines = INPUT_FILE.read_text(encoding="utf-8").splitlines()
    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        parts = line.split(" | ")
        if len(parts) >= 2:
            entries.append((parts[0].strip(), parts[1].strip(), parts[2].strip() if len(parts) > 2 else ""))
        else:
            entries.append((line, "", ""))
    return entries


def save_unprocessed(entries: list[tuple[str, str, str]]) -> None:
    with INPUT_FILE.open("w", encoding="utf-8") as f:
        for title, author, status in entries:
            f.write(f"{title} | {author} | {status}\n")


def find_asin_audible(title: str, author: str) -> list[str]:
    params = {
        "title": title,
        "author": author,
        "num_results": 10,
        "page": 0,
        "response_groups": "contributors,product_desc,product_attrs,product_details,media",
    }
    try:
        resp = requests.get(AUDIBLE_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"  Audible error: {exc}")
        return []

    asins: list[str] = []
    for p in data.get("products", []):
        asin = p.get("asin")
        if asin and asin not in asins:
            asins.append(asin)

    return asins[:5]


def fetch_chapters_from_audnexus(asin: str, region: str = "US") -> list[Chapter] | None:
    url = AUDNEXUS_URL.format(asin=asin.upper())
    params = {"region": region.lower()}
    time.sleep(0.15)
    resp = requests.get(url, params=params, timeout=30)
    if resp.status_code in (400, 404):
        return None
    if resp.status_code == 429:
        retry_after = float(resp.headers.get("Retry-After", "5"))
        print(f"  Rate limited, waiting {retry_after}s...")
        time.sleep(retry_after)
        resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    chapters_data = data.get("chapters", [])
    if not chapters_data:
        return None
    chapters = []
    for item in chapters_data:
        start_ms = item.get("startOffsetMs")
        length_ms = item.get("lengthMs")
        if start_ms is None or length_ms is None:
            continue
        start = float(start_ms) / 1000.0
        end = start + (float(length_ms) / 1000.0)
        chapters.append(
            Chapter(
                title=(item.get("title") or "").strip() or f"Chapter {len(chapters) + 1}",
                start_seconds=start,
                end_seconds=end,
            )
        )
    return chapters if chapters else None


def write_chapters_json_sidecar(
    book_dir: Path,
    m4b_filename: str,
    chapters: list[Chapter],
    duration_seconds: float,
) -> Path:
    audiosilo_dir = book_dir / ".audiosilo"
    audiosilo_dir.mkdir(parents=True, exist_ok=True)
    chapters_list = []
    for i, c in enumerate(chapters):
        start_ms = int(round(c.start_seconds * 1000))
        length_ms = int(round((c.end_seconds - c.start_seconds) * 1000))
        chapters_list.append({
            "title": c.title,
            "start_ms": start_ms,
            "length_ms": length_ms,
        })
    m4b_path = book_dir / m4b_filename
    file_size = m4b_path.stat().st_size if m4b_path.exists() else 0
    file_entries = [
        {"path": m4b_filename, "size": file_size, "duration_ms": int(duration_seconds * 1000)}
    ]
    chapter_list = {
        "version": 1,
        "generator": "chapter-sidecar",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "from": "narration",
        "files": file_entries,
        "chapters": chapters_list,
    }
    chapters_json_path = audiosilo_dir / "chapters.json"
    with open(chapters_json_path, "w", encoding="utf-8") as f:
        json.dump(chapter_list, f, indent=2, ensure_ascii=False)
    return chapters_json_path


def trigger_rescan(client: AudioSiloClient, book_path: str) -> dict:
    config = load_config()
    resp = client._post(
        f"/api/v1/admin/libraries/{config.audiosilo_library_id}/book/rescan",
        params={"path": book_path},
    )
    return resp


def process_book_with_asin(
    client: AudioSiloClient,
    book: dict,
    chapters_list: list[Chapter],
    duration: float,
) -> tuple[int, str]:
    title = book.get("title", "Unknown")
    author = book.get("author", "Unknown")
    files = book.get("files", [])
    if files:
        m4b_filename = files[0].get("path", "").split("/")[-1]
    else:
        book_path = book.get("path", "")
        m4b_filename = f"{book_path.split('/')[-1]}.m4b"
    book_folder = Path("B:/Audiobooks") / book.get("path", "")
    m4b_path = book_folder / m4b_filename
    if not m4b_path.exists():
        m4b_files = list(book_folder.glob("*.m4b"))
        if m4b_files:
            m4b_filename = m4b_files[0].name
            m4b_path = book_folder / m4b_filename
    write_chapters_json_sidecar(book_folder, m4b_filename, chapters_list, duration or 0)
    try:
        result = trigger_rescan(client, book.get("path", ""))
        saved_book = result.get("book", {})
        cc = saved_book.get("chapter_count")
        cs = saved_book.get("chapters_source")
        print(f"  Rescan result: chapter_count={cc}, chapters_source={cs}")
        if cc and cc > 1:
            return 0, "success"
        return 1, "write_failed"
    except Exception as exc:
        print(f"  Rescan trigger failed: {exc}")
        return 1, "write_failed"


def main() -> int:
    config = load_config()
    client = AudioSiloClient(config)
    entries = load_unprocessed()
    if not entries:
        print("No entries in unprocessed_titles.txt. Nothing to do.")
        return 0

    no_asin_entries = [(t, a, s) for t, a, s in entries if s == "no_asin"]
    print(f"Total entries: {len(entries)}")
    print(f"Books with no_asin status: {len(no_asin_entries)}")
    print()

    remaining: list[tuple[str, str, str]] = []
    succeeded = 0
    skipped = 0
    failed = 0

    for t, a, s in entries:
        if s != "no_asin":
            remaining.append((t, a, s))

    for index, (title, author, status) in enumerate(no_asin_entries, 1):
        print(f"[{index}/{len(no_asin_entries)}] '{title}' by {author}")
        print("-" * 70)

        asins = find_asin_audible(title, author)
        if not asins:
            print(f"  SKIP: No ASIN found via Audible catalogue search.")
            remaining.append((title, author, "no_asin"))
            skipped += 1
            print()
            time.sleep(REQUEST_DELAY)
            continue

        asin = asins[0]
        print(f"  Found ASIN: {asin}")

        print(f"  Looking up chapters from Audnexus (ASIN: {asin})...")
        chapters = fetch_chapters_from_audnexus(asin)
        if not chapters:
            print(f"  SKIP: No chapters found from Audnexus.")
            remaining.append((title, author, "no_chapters_from_audnexus"))
            skipped += 1
            print()
            time.sleep(REQUEST_DELAY)
            continue

        print(f"  Found {len(chapters)} chapters from Audnexus")

        book = None
        for attempt in range(3):
            try:
                all_books = client.list_books_all()
                for b in all_books:
                    if b.get("title", "").lower() == title.lower() and b.get("author", "").lower() == author.lower():
                        book = b
                        break
                if book:
                    break
            except Exception as exc:
                print(f"  Error fetching books (attempt {attempt + 1}): {exc}")
                time.sleep(2)
        if not book:
            print(f"  SKIP: Could not find book record in AudioSilo library.")
            remaining.append((title, author, "no_book_record"))
            skipped += 1
            print()
            time.sleep(REQUEST_DELAY)
            continue

        print(f"  Found book: {book.get('title')} by {book.get('author')}")
        print(f"  Path: {book.get('path')}")
        print(f"  Duration: {book.get('duration')}s")

        duration = book.get("duration")
        if duration:
            try:
                chapters = validate_chapters(chapters, audiobook_duration=duration, tolerance=5.0)
                print(f"  Validated {len(chapters)} chapters against duration {duration}s")
            except ValueError as exc:
                print(f"  Validation warning: {exc}")
                clipped = []
                for ch in chapters:
                    if ch.start_seconds >= duration:
                        continue
                    end = min(ch.end_seconds, duration)
                    if end <= ch.start_seconds:
                        continue
                    clipped.append(Chapter(
                        title=ch.title,
                        start_seconds=ch.start_seconds,
                        end_seconds=end,
                    ))
                chapters = clipped
                print(f"  Clipped {len(chapters)} chapters to duration {duration}s")

        code, book_status = process_book_with_asin(client, book, chapters, duration or 0)
        if code == 0:
            succeeded += 1
            print(f"  SUCCESS: Chapters written and verified.")
        else:
            failed += 1
            remaining.append((title, author, book_status))
            print(f"  FAILED: {book_status}")

        print()
        time.sleep(REQUEST_DELAY)

    print()
    print("=" * 70)
    print(f"Dev batch complete: {succeeded} succeeded, {skipped} skipped, {failed} failed")
    save_unprocessed(remaining)
    if remaining:
        print(f"Updated {INPUT_FILE} with {len(remaining)} remaining unprocessed books.")
    else:
        print("All books processed successfully.")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())