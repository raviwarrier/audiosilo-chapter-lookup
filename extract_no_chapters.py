"""
extract_no_chapters.py - Extract AudioSilo 'no_chapters' health list to TXT

What it does:
  Queries the AudioSilo admin API for books flagged with issue=no_chapters
  (books with duration > 2h and fewer than 2 chapters), handles pagination,
  and writes "Title | Author" lines to no_chapters.txt.

  This file is the input list for process_book.py --batch-file / --file.

Depends on:
  - chapter_sidecar.config.load_config  (.env: AUDIOSILO_BASE_URL, AUDIOSILO_API_TOKEN, AUDIOSILO_LIBRARY_ID)
  - requests                            (HTTP client to AudioSilo admin API)
  - AudioSilo server at AUDIOSILO_BASE_URL

Output:
  no_chapters.txt  (one book per line: "Title | Author")
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

from chapter_sidecar.config import load_config


OUTPUT_FILE = Path("no_chapters.txt")
PAGE_LIMIT = 200


def extract_no_chapters(output_file: Path = OUTPUT_FILE) -> int:
    """Fetch all books with issue=no_chapters and write title | author lines.

    Returns the number of books written.
    """
    config = load_config()
    base_url = config.audiosilo_base_url.rstrip("/")
    token = config.audiosilo_api_token
    library_id = config.audiosilo_library_id

    if not token:
        print("ERROR: AUDIOSILO_API_TOKEN is not set in .env", file=sys.stderr)
        return 0

    headers = {"Authorization": f"Bearer {token}"}
    cursor = None
    total = 0

    with output_file.open("w", encoding="utf-8") as f:
        while True:
            params: dict[str, str | int] = {
                "issue": "no_chapters",
                "library_id": str(library_id),
                "limit": str(PAGE_LIMIT),
            }
            if cursor:
                params["cursor"] = cursor

            resp = requests.get(
                f"{base_url}/api/v1/admin/books",
                headers=headers,
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            books = data.get("books", [])
            for book in books:
                title = (book.get("title") or "").strip()
                author = (book.get("author") or "").strip()
                if not title:
                    title = (book.get("path") or "").strip()
                if title:
                    f.write(f"{title} | {author}\n")
                    total += 1

            cursor = data.get("next_cursor")
            if not cursor:
                break

    print(f"Wrote {total} books to {output_file}")
    return total


if __name__ == "__main__":
    count = extract_no_chapters()
    if count == 0:
        print("No books found in no_chapters category.", file=sys.stderr)
        sys.exit(1)
    print("Done.")
