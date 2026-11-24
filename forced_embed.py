from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

from chapter_sidecar.audiosilo_client import AudioSiloClient
from chapter_sidecar.chapters import Chapter, validate_chapters
from chapter_sidecar.config import load_config

from extract_no_chapters import extract_no_chapters

AUDIOBOOKS_ROOT = Path("B:/Audiobooks")

try:
    import imageio_ffmpeg
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except ImportError:
    FFMPEG_PATH = "ffmpeg"


def _normalize_text(value: str) -> str:
    if not value:
        return ""
    value = value.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    value = value.replace("\u2014", "-").replace("\u2013", "-")
    return " ".join(value.lower().strip().split())


def find_book(client: AudioSiloClient, title: str, author: str) -> dict | None:
    config = load_config()
    headers = {"Authorization": "Bearer " + config.audiosilo_api_token}
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


def find_asin_audible(title: str, author: str) -> str | None:
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
        print("  Audible error: " + str(exc))
        return None

    for p in data.get("products", []):
        asin = p.get("asin")
        if asin:
            return asin

    return None


def fetch_chapters_from_audnexus(asin: str, region: str = "uk") -> list[Chapter] | None:
    for try_region in ["uk", "us", "ca", "au", "de", "es", "fr", "in", "it", "jp"]:
        url = "https://api.audnex.us/books/" + asin.upper() + "/chapters"
        params = {"region": try_region}

        time.sleep(0.15)

        try:
            resp = requests.get(url, params=params, timeout=30)
        except Exception as exc:
            print("  Audnexus request error (" + try_region + "): " + str(exc))
            continue

        if resp.status_code == 404:
            continue
        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", "5"))
            print("  Rate limited, waiting " + str(retry_after) + "s...")
            time.sleep(retry_after)
            resp = requests.get(url, params=params, timeout=30)
        if resp.status_code != 200:
            continue

        data = resp.json()
        chapters_data = data.get("chapters", [])
        if not chapters_data:
            continue

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

        if chapters:
            print("  Found chapters in region: " + try_region)
            return chapters if chapters else None

    return None


def write_chapters_txt(chapters: list[Chapter], output_path: Path) -> None:
    lines = []
    lines.append(";FFMETADATA1")
    for i, ch in enumerate(chapters):
        start_ms = int(ch.start_seconds * 1000)
        end_ms = int(ch.end_seconds * 1000)
        lines.append("")
        lines.append("CHAPTER=" + str(i))
        lines.append("TIMEBASE=1/1000")
        lines.append("START=" + str(start_ms))
        lines.append("END=" + str(end_ms))
        lines.append("title=" + ch.title)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def embed_chapters_ffmpeg(m4b_path: Path, chapters_txt: Path) -> bool:
    output_path = Path(str(m4b_path) + ".tmp.m4b")

    cmd = [
        FFMPEG_PATH,
        "-y",
        "-i", str(m4b_path),
        "-i", str(chapters_txt),
        "-map", "0",
        "-map_metadata", "1",
        "-c", "copy",
        "-f", "mp4",
        str(output_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        print("  ffmpeg timed out (>300s)")
        return False
    except FileNotFoundError:
        print("  ffmpeg not found on PATH. Install ffmpeg to use this script.")
        return False

    if result.returncode != 0:
        print("  ffmpeg failed:")
        print(result.stderr[-2000:] if result.stderr else "no stderr")
        return False

    try:
        m4b_path.unlink()
        output_path.rename(m4b_path)
    except Exception as exc:
        print("  Failed to replace original file: " + str(exc))
        try:
            output_path.unlink()
        except Exception:
            pass
        return False

    return True


def process_book_with_asin(
    client: AudioSiloClient,
    book: dict,
    chapters: list[Chapter],
    duration: float,
) -> tuple[int, str]:
    import shutil

    title = book.get("title", "Unknown")
    author = book.get("author", "Unknown")

    files = book.get("files", [])
    if files:
        m4b_filename = files[0].get("path", "").split("/")[-1]
    else:
        book_path = book.get("path", "")
        m4b_filename = book_path.split("/")[-1] + ".m4b"

    book_folder = AUDIOBOOKS_ROOT / book.get("path", "")

    m4b_path = book_folder / m4b_filename
    if not m4b_path.exists():
        m4b_files = list(book_folder.glob("*.m4b"))
        if m4b_files:
            m4b_filename = m4b_files[0].name
            m4b_path = book_folder / m4b_filename
        else:
            print("  SKIP: No .m4b file found in " + str(book_folder))
            return 2, "no_m4b_file"

    print("  Found book: " + book.get("title", ""))
    print("  Path: " + book.get("path", ""))
    print("  Duration: " + str(book.get("duration")) + "s")
    print("  Chapters: " + str(len(chapters)))

    chapters_txt = Path(tempfile.gettempdir()) / (m4b_filename + ".chapters.txt")
    write_chapters_txt(chapters, chapters_txt)

    backup_path = Path(str(m4b_path) + ".bak")
    if not backup_path.exists():
        shutil.copy2(str(m4b_path), str(backup_path))
        print("  Backup created: " + str(backup_path.name))

    print("  Embedding chapters via ffmpeg...")
    success = embed_chapters_ffmpeg(m4b_path, chapters_txt)

    try:
        chapters_txt.unlink()
    except Exception:
        pass

    if not success:
        try:
            shutil.copy2(str(backup_path), str(m4b_path))
            print("  Restored original from backup")
        except Exception as exc:
            print("  Failed to restore backup: " + str(exc))
            return 1, "write_failed"

    try:
        backup_path.unlink()
    except Exception:
        pass

    print("  SUCCESS: Chapters embedded in " + m4b_filename)
    return 0, "success"


def main() -> int:
    config = load_config()
    client = AudioSiloClient(config)

    import argparse

    parser = argparse.ArgumentParser(
        description="Embed chapters from Audnexus into m4b audio files via ffmpeg"
    )
    parser.add_argument("--title", help="Book title (single mode)")
    parser.add_argument("--author", help="Author name (single mode)")
    parser.add_argument(
        "--batch-file",
        "--file",
        help="Path to a TXT file with 'title | author' lines (batch mode)",
    )
    args = parser.parse_args()

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
    print("Searching for: '" + title + "' by " + author)
    book = find_book(client, title, author)

    if not book:
        print("Book not found: '" + title + "' by " + author)
        return 1

    code, status = _process_book(client, book)
    return code


def _process_batch(client: AudioSiloClient, batch_file: str) -> int:
    path = Path(batch_file)
    if not path.exists():
        print("ERROR: Batch file not found: " + batch_file, file=sys.stderr)
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

    print("Batch mode: " + str(len(entries)) + " books to process from " + batch_file)
    print()

    succeeded = 0
    failed = 0
    skipped = 0
    unprocessed = []

    for index, (title, author) in enumerate(entries, 1):
        print("[" + str(index) + "/" + str(len(entries)) + "] Processing: '" + title + "' by " + author)
        print("-" * 70)

        book = find_book(client, title, author)
        if not book:
            print("  SKIP: Book not found in library: '" + title + "' by " + author)
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
    print("Batch complete: " + str(succeeded) + " succeeded, " + str(skipped) + " skipped, " + str(failed) + " failed")

    unprocessed_file = Path("unprocessed_titles.txt")
    if unprocessed:
        with unprocessed_file.open("w", encoding="utf-8") as f:
            for title, author, status in unprocessed:
                f.write(title + " | " + author + " | " + status + "\n")
        print("Wrote " + str(len(unprocessed)) + " unprocessed titles to " + str(unprocessed_file))
    else:
        if unprocessed_file.exists():
            unprocessed_file.unlink()
        print("All books processed successfully. No unprocessed titles file written.")

    return 0 if failed == 0 else 1


def _process_book(client: AudioSiloClient, book: dict) -> tuple[int, str]:
    title = book.get("title", "Unknown")
    author = book.get("author", "Unknown")

    print("  Found: " + title + " by " + author)
    print("  Path: " + book.get("path", ""))
    print("  Duration: " + str(book.get("duration")) + "s")
    print("  Current chapter_count: " + str(book.get("chapter_count")))
    print("  ASIN: " + str(book.get("asin")))
    print("  ISBN: " + str(book.get("isbn")))
    print("  chapters_source: " + str(book.get("chapters_source")))
    print()

    if book.get("chapter_count", 0) > 1:
        print("  SKIP: Book already has chapters.")
        return 2, "already_has_chapters"

    asin = book.get("asin")
    if not asin:
        print("  No ASIN in book record. Searching Audible catalogue for '" + title + "' by " + author + "...")
        asin = find_asin_audible(title, author)
        if not asin:
            print("  SKIP: No ASIN found via Audible catalogue search.")
            return 2, "no_asin"
        print("  Found ASIN via Audible: " + asin)

    print("  Looking up chapters from Audnexus (ASIN: " + asin + ", region: US)...")
    chapters = fetch_chapters_from_audnexus(asin)

    if not chapters:
        print("  SKIP: No chapters found from Audnexus.")
        return 2, "no_chapters_from_audnexus"

    print("  Found " + str(len(chapters)) + " chapters from Audnexus")

    duration = book.get("duration")
    if duration:
        try:
            chapters = validate_chapters(chapters, audiobook_duration=duration, tolerance=5.0)
            print("  Validated " + str(len(chapters)) + " chapters against duration " + str(duration) + "s")
        except ValueError as exc:
            print("  Validation warning: " + str(exc))
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
            print("  Clipped " + str(len(chapters)) + " chapters to duration " + str(duration) + "s")

    return process_book_with_asin(client, book, chapters, duration or 0)


if __name__ == "__main__":
    sys.exit(main())