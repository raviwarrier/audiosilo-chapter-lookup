"""
process_book.py - AudioSilo chapter import script (single + batch mode)

What it does:
  Finds a book in the AudioSilo library by title/author, looks up chapters
  from Audnexus (external provider), writes a .audiosilo/chapters.json sidecar
  next to the audio file, and triggers an AudioSilo rescan so the server
  ingests the chapters.

Modes:
  Single:  --title "Title" --author "Author"
  Batch:   --file no_chapters.txt  (or --batch-file no_chapters.txt)
           Each line: "Title | Author"
  Auto:    (no arguments) Extracts fresh no_chapters list from AudioSilo,
           then batch processes all of them.

Depends on:
  - chapter_sidecar.audiosilo_client.AudioSiloClient  (API client)
  - chapter_sidecar.chapters.Chapter, validate_chapters  (data model + validation)
  - chapter_sidecar.config.load_config                (.env configuration)
  - extract_no_chapters.extract_no_chapters           (health-list extraction)
  - requests                                            (HTTP client)
  - find_asin_audible()                                 (Audible catalogue ASIN lookup)
  - AudioSilo server at AUDIOSILO_BASE_URL (admin API)
  - Audible catalogue API at https://api.audible.com/1.0/catalog/products (ASIN lookup)
  - Audnexus API at https://api.audnex.us               (chapter source)

Returns:
  0 = success, 1 = error, 2 = skipped (already has chapters / no ASIN / no chapters)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from chapter_sidecar.audiosilo_client import AudioSiloClient
from chapter_sidecar.chapters import Chapter, validate_chapters
from chapter_sidecar.config import load_config

from extract_no_chapters import extract_no_chapters


def _normalize_text(value: str) -> str:
    """Normalize text for comparison: lowercase, strip, normalize quotes/dashes."""
    if not value:
        return ""
    # Normalize curly quotes to straight quotes
    value = value.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    # Normalize em/en dashes to hyphen
    value = value.replace("\u2014", "-").replace("\u2013", "-")
    return " ".join(value.lower().strip().split())


def find_book(client: AudioSiloClient, title: str, author: str) -> dict | None:
    """Search the library for a book by title and author."""
    config = load_config()
    headers = {"Authorization": f"Bearer {config.audiosilo_api_token}"}
    cursor = None

    title_norm = _normalize_text(title)
    author_norm = _normalize_text(author)

    while True:
        params = {"library_id": config.audiosilo_library_id}
        if cursor:
            params["cursor"] = cursor

        resp = requests.get(
            f"{config.audiosilo_base_url}/api/v1/admin/books",
            headers=headers,
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        for book in data.get("books", []):
            if (
                _normalize_text(book.get("title", "")) == title_norm
                and _normalize_text(book.get("author", "")) == author_norm
            ):
                return book

        cursor = data.get("next_cursor")
        if not cursor:
            break

    return None


def fetch_chapters_from_audnexus(asin: str, region: str = "US") -> list[Chapter] | None:
    """Fetch chapters from Audnexus for a given ASIN."""
    url = f"https://api.audnex.us/books/{asin.upper()}/chapters"
    params = {"region": region.lower()}

    time.sleep(0.15)  # throttle: one request per 150 ms

    resp = requests.get(url, params=params, timeout=30)
    if resp.status_code == 404:
        return None
    if resp.status_code == 429:
        retry_after = float(resp.headers.get("Retry-After", "5"))
        print(f"Rate limited, waiting {retry_after}s...")
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


def find_asin_audible(title: str, author: str) -> str | None:
    """Search Audible catalogue API by title+author to find an ASIN.

    Returns the first matching ASIN, or None if none found.
    """
    AUDIBLE_URL = "https://api.audible.com/1.0/catalog/products"
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
        return None

    for p in data.get("products", []):
        asin = p.get("asin")
        if asin:
            return asin

    return None


def write_chapters_json_sidecar(
    book_dir: Path,
    m4b_filename: str,
    chapters: list[Chapter],
    duration_seconds: float,
) -> Path:
    """Write .audiosilo/chapters.json sidecar file next to the audio file."""
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
    """Trigger a rescan of the book via AudioSilo API."""
    config = load_config()
    resp = client._post(
        f"/api/v1/admin/libraries/{config.audiosilo_library_id}/book/rescan",
        params={"path": book_path},
    )
    return resp


def main() -> int:
    parser = argparse.ArgumentParser(description="Process a book: add chapters via Audnexus + chapters.json sidecar")
    parser.add_argument("--title", help="Book title (single mode)")
    parser.add_argument("--author", help="Author name (single mode)")
    parser.add_argument(
        "--batch-file",
        "--file",
        help="Path to a TXT file with 'title | author' lines (batch mode)",
    )
    args = parser.parse_args()

    config = load_config()
    client = AudioSiloClient(config)

    # No arguments: auto-extract fresh no_chapters list and batch process
    if not args.title and not args.batch_file:
        print("No arguments provided. Extracting fresh no_chapters list from AudioSilo...")
        print("-" * 70)
        count = extract_no_chapters()
        if count == 0:
            print("No books found in no_chapters category. Nothing to process.")
            return 0
        print("-" * 70)
        print()
        return _process_batch(client, "no_chapters.txt")

    if args.title and not args.author:
        print("ERROR: --title requires --author", file=sys.stderr)
        return 1
    if args.author and not args.title:
        print("ERROR: --author requires --title", file=sys.stderr)
        return 1

    if args.batch_file:
        return _process_batch(client, args.batch_file)

    return _process_single(client, args.title, args.author)


def _process_single(client: AudioSiloClient, title: str, author: str) -> int:
    """Process one book by title and author."""
    print(f"Searching for: '{title}' by {author}")
    book = find_book(client, title, author)

    if not book:
        print(f"Book not found: '{title}' by {author}")
        return 1

    _code, _status = _process_book(client, book)
    return _code


def _process_batch(client: AudioSiloClient, batch_file: str) -> int:
    """Process all books listed in a 'title | author' TXT file."""
    path = Path(batch_file)
    if not path.exists():
        print(f"ERROR: Batch file not found: {batch_file}", file=sys.stderr)
        return 1

    lines = path.read_text(encoding="utf-8").splitlines()
    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if " | " in line:
            title, author = line.split(" | ", 1)
        else:
            title, author = line, ""
        entries.append((title.strip(), author.strip()))

    print(f"Batch mode: {len(entries)} books to process from {batch_file}")
    print()

    succeeded = 0
    failed = 0
    skipped = 0
    unprocessed: list[tuple[str, str, str]] = []

    for index, (title, author) in enumerate(entries, 1):
        print(f"[{index}/{len(entries)}] Processing: '{title}' by {author}")
        print("-" * 70)

        book = find_book(client, title, author)
        if not book:
            print(f"  SKIP: Book not found in library: '{title}' by {author}")
            print()
            failed += 1
            unprocessed.append((title, author, "not_found"))
            continue

        code, status = _process_book(client, book)
        if code == 0:
            succeeded += 1
        elif code == 2:
            skipped += 1
            unprocessed.append((title, author, status))
        else:
            failed += 1
            unprocessed.append((title, author, status))
        print()

    print("=" * 70)
    print(f"Batch complete: {succeeded} succeeded, {skipped} skipped, {failed} failed")

    # Write unprocessed titles file
    unprocessed_file = Path("unprocessed_titles.txt")
    if unprocessed:
        with unprocessed_file.open("w", encoding="utf-8") as f:
            for title, author, status in unprocessed:
                f.write(f"{title} | {author} | {status}\n")
        print(f"Wrote {len(unprocessed)} unprocessed titles to {unprocessed_file}")
    else:
        # Remove stale file if everything succeeded
        if unprocessed_file.exists():
            unprocessed_file.unlink()
        print("All books processed successfully. No unprocessed titles file written.")

    return 0 if failed == 0 else 1


def _process_book(client: AudioSiloClient, book: dict) -> tuple[int, str]:
    """Process a single book record: lookup chapters, write sidecar, trigger rescan.

    Returns:
  (0, "success") - chapters written and verified
  (1, "write_failed") - rescan trigger failed or other error
  (2, status_str) - skipped with reason in status_str

Batch mode writes unprocessed_titles.txt with all non-success books
(format: "Title | Author | status").
    """
    title = book.get("title", "Unknown")
    author = book.get("author", "Unknown")

    print(f"  Found: {title} by {author}")
    print(f"  Path: {book.get('path')}")
    print(f"  Duration: {book.get('duration')}s")
    print(f"  Current chapter_count: {book.get('chapter_count')}")
    print(f"  ASIN: {book.get('asin')}")
    print(f"  ISBN: {book.get('isbn')}")
    print(f"  chapters_source: {book.get('chapters_source')}")
    print()

    if book.get("chapter_count", 0) > 1:
        print("  SKIP: Book already has chapters.")
        return 2, "already_has_chapters"

    asin = book.get("asin")
    if not asin:
        print(f"  No ASIN in book record. Searching Audible catalogue for '{title}' by {author}...")
        asin = find_asin_audible(title, author)
        if not asin:
            print("  SKIP: No ASIN found via Audible catalogue search.")
            return 2, "no_asin"
        print(f"  Found ASIN via Audible: {asin}")

    print(f"  Looking up chapters from Audnexus (ASIN: {asin}, region: US)...")
    chapters = fetch_chapters_from_audnexus(asin)

    if not chapters:
        print("  SKIP: No chapters found from Audnexus.")
        return 2, "no_chapters_from_audnexus"

    print(f"  Found {len(chapters)} chapters from Audnexus")

    duration = book.get("duration")
    if duration:
        try:
            chapters = validate_chapters(chapters, audiobook_duration=duration, tolerance=5.0)
            print(f"  Validated {len(chapters)} chapters against duration {duration}s")
        except ValueError as exc:
            print(f"  Validation warning: {exc}")
            # Clip chapters to audio duration: drop chapters starting at/beyond
            # duration, cap end times at duration
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

    files = book.get("files", [])
    if files:
        m4b_filename = files[0].get("path", "").split("/")[-1]
    else:
        book_path = book.get("path", "")
        m4b_filename = f"{book_path.split('/')[-1]}.m4b"

    book_folder = Path("B:/Audiobooks") / book.get("path", "")

    # Verify the m4b file exists at the expected path; if not, search for any .m4b
    m4b_path = book_folder / m4b_filename
    if not m4b_path.exists():
        m4b_files = list(book_folder.glob("*.m4b"))
        if m4b_files:
            m4b_filename = m4b_files[0].name
            m4b_path = book_folder / m4b_filename

    print(f"  Writing chapters.json to .audiosilo/ next to {m4b_filename}...")
    chapters_json_path = write_chapters_json_sidecar(
        book_folder,
        m4b_filename,
        chapters,
        duration or 0,
    )
    print(f"  Written: {chapters_json_path}")

    print("  Triggering rescan via AudioSilo API...")
    try:
        result = trigger_rescan(client, book.get("path", ""))
        saved_book = result.get("book", {})
        print(f"  Rescan result: chapter_count={saved_book.get('chapter_count')}")
        print(f"    chapters_source: {saved_book.get('chapters_source')}")
        print(f"    generated_chapters_fit: {saved_book.get('generated_chapters_fit')}")
    except Exception as exc:
        print(f"  Rescan trigger failed: {exc}")
        print("  You may need to trigger a rescan manually in the AudioSilo admin UI.")
        return 1, "write_failed"

    print("  === DONE ===")
    print(f"  Check the AudioSilo UI for '{title}' by {author}")
    return 0, "success"


if __name__ == "__main__":
    sys.exit(main())