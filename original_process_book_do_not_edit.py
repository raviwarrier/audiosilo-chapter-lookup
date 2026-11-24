"""Process a single book: find chapters from Audnexus, write chapters.json sidecar, trigger rescan.

Usage (PowerShell):
    python process_book.py --title "Book Title" --author "Author Name"
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


def find_book(client: AudioSiloClient, title: str, author: str) -> dict | None:
    """Search the library for a book by title and author."""
    config = load_config()
    headers = {"Authorization": f"Bearer {config.audiosilo_api_token}"}
    cursor = None

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
            if book.get("title", "").lower() == title.lower() and book.get("author", "").lower() == author.lower():
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
    parser.add_argument("--title", required=True, help="Book title")
    parser.add_argument("--author", required=True, help="Author name")
    args = parser.parse_args()

    config = load_config()
    client = AudioSiloClient(config)

    print(f"Searching for: '{args.title}' by {args.author}")
    book = find_book(client, args.title, args.author)

    if not book:
        print(f"Book not found: '{args.title}' by {args.author}")
        return 1

    print(f"Found: {book.get('title')} by {book.get('author')}")
    print(f"  Path: {book.get('path')}")
    print(f"  Duration: {book.get('duration')}s")
    print(f"  Current chapter_count: {book.get('chapter_count')}")
    print(f"  ASIN: {book.get('asin')}")
    print(f"  ISBN: {book.get('isbn')}")
    print(f"  chapters_source: {book.get('chapters_source')}")
    print()

    if book.get("chapter_count", 0) > 1:
        print("Book already has chapters.")
        return 0

    asin = book.get("asin")
    if not asin:
        print("No ASIN found in book record. Cannot lookup chapters.")
        return 1

    print(f"Looking up chapters from Audnexus (ASIN: {asin}, region: US)...")
    chapters = fetch_chapters_from_audnexus(asin)

    if not chapters:
        print("No chapters found from Audnexus.")
        return 0

    print(f"Found {len(chapters)} chapters from Audnexus")

    duration = book.get("duration")
    if duration:
        try:
            chapters = validate_chapters(chapters, audiobook_duration=duration, tolerance=5.0)
            print(f"Validated {len(chapters)} chapters against duration {duration}s")
        except ValueError as exc:
            print(f"Validation warning: {exc}")
            print("Proceeding with unvalidated chapters...")

    files = book.get("files", [])
    if files:
        m4b_filename = files[0].get("path", "").split("/")[-1]
    else:
        book_path = book.get("path", "")
        m4b_filename = f"{book_path.split('/')[-1]}.m4b"

    book_folder = Path("B:/Audiobooks") / book.get("path", "")

    print(f"Writing chapters.json to .audiosilo/ next to {m4b_filename}...")
    chapters_json_path = write_chapters_json_sidecar(
        book_folder,
        m4b_filename,
        chapters,
        duration or 0,
    )
    print(f"  Written: {chapters_json_path}")

    print("Triggering rescan via AudioSilo API...")
    try:
        result = trigger_rescan(client, book.get("path", ""))
        saved_book = result.get("book", {})
        print(f"Rescan result: chapter_count={saved_book.get('chapter_count')}")
        print(f"  chapters_source: {saved_book.get('chapters_source')}")
        print(f"  generated_chapters_fit: {saved_book.get('generated_chapters_fit')}")
    except Exception as exc:
        print(f"Rescan trigger failed: {exc}")
        print("You may need to trigger a rescan manually in the AudioSilo admin UI.")

    print()
    print("=== DONE ===")
    print(f"Check the AudioSilo UI for '{args.title}' by {args.author}")
    return 0


if __name__ == "__main__":
    sys.exit(main())