"""Direct chapter writing to AudioSilo's SQLite database or audio files.

WARNING: This bypasses AudioSilo's API and writes directly to the database
or audio files. This is for TESTING ONLY and should not be used in
production. Always back up before writing.

Backup/restore workflow:
1. backup_database() or backup_audiofile() - copies the target to a .bak file
2. write_chapters_to_db() or write_chapters_to_audiofile() - performs the write
3. verify_write() - re-reads and confirms the chapters are present
4. User checks the AudioSilo UI
5. delete_backup() - removes the backup once confirmed good
6. restore_backup() - reverts if something went wrong
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .chapters import Chapter


@dataclass
class DirectWriteConfig:
    enabled: bool = False
    method: str = "db"  # "db" | "audiofile"
    db_path: str = ""
    backup_dir: str = ""  # empty = backups next to target file
    audio_file_path: str = ""  # full path to the m4b file for audiofile method


@dataclass
class BackupRecord:
    original_path: str
    backup_path: str
    created_at: str
    method: str


class DirectWriter:
    """Handles direct writing of chapters with backup/restore safety."""

    def __init__(self, config: DirectWriteConfig):
        self.config = config
        self._backups: dict[str, BackupRecord] = {}
        self._load_existing_backups()

    def _load_existing_backups(self) -> None:
        if self.config.backup_dir:
            bak_dir = Path(self.config.backup_dir)
            if not bak_dir.exists():
                return
            for bak_file in bak_dir.glob("*.bak"):
                rel_name = bak_file.stem
                parts = rel_name.split("_", 1)
                original = parts[1] if len(parts) >= 2 else rel_name
                self._backups[original] = BackupRecord(
                    original_path=original,
                    backup_path=str(bak_file),
                    created_at=bak_file.stat().st_mtime,
                    method="db" if ".sqlite" in original or ".db" in original else "audiofile",
                )

    def _timestamp(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    def _backup_name(self, original_path: str) -> str:
        ts = self._timestamp()
        safe = original_path.replace(os.sep, "_").replace(":", "_")
        return f"{ts}_{safe}.bak"

    def backup_directory(self, original_path: str) -> Path:
        """Return the directory where backups for this file should go.

        By default, backups are stored next to the target file (same directory),
        so the user can manually replace files when needed. Override via
        config.backup_dir if a separate directory is preferred.
        """
        if self.config.backup_dir:
            return Path(self.config.backup_dir)
        return Path(original_path).parent

    def backup_database(self, db_path: str) -> BackupRecord:
        src = Path(db_path)
        if not src.exists():
            raise FileNotFoundError(f"Database file not found: {db_path}")
        bak_name = self._backup_name(db_path)
        bak_dir = self.backup_directory(db_path)
        bak_dir.mkdir(parents=True, exist_ok=True)
        bak_path = bak_dir / bak_name
        shutil.copy2(src, bak_path)
        record = BackupRecord(
            original_path=db_path,
            backup_path=str(bak_path),
            created_at=datetime.now(timezone.utc).isoformat(),
            method="db",
        )
        self._backups[db_path] = record
        return record

    def backup_audiofile(self, file_path: str) -> BackupRecord:
        src = Path(file_path)
        if not src.exists():
            raise FileNotFoundError(f"Audio file not found: {file_path}")
        bak_name = self._backup_name(file_path)
        bak_dir = self.backup_directory(file_path)
        bak_dir.mkdir(parents=True, exist_ok=True)
        bak_path = bak_dir / bak_name
        shutil.copy2(src, bak_path)
        record = BackupRecord(
            original_path=file_path,
            backup_path=str(bak_path),
            created_at=datetime.now(timezone.utc).isoformat(),
            method="audiofile",
        )
        self._backups[file_path] = record
        return record

    def restore_backup(self, original_path: str) -> None:
        record = self._backups.get(original_path)
        if not record:
            for rec in self._backups.values():
                if rec.original_path == original_path:
                    record = rec
                    break
        if not record:
            raise FileNotFoundError(f"No backup found for: {original_path}")
        bak = Path(record.backup_path)
        if not bak.exists():
            raise FileNotFoundError(f"Backup file not found: {record.backup_path}")
        shutil.copy2(bak, Path(original_path))

    def delete_backup(self, original_path: str) -> None:
        record = self._backups.get(original_path)
        if record:
            bak = Path(record.backup_path)
            if bak.exists():
                bak.unlink()
            del self._backups[original_path]
        else:
            for orig, rec in list(self._backups.items()):
                if rec.original_path == original_path:
                    bak = Path(rec.backup_path)
                    if bak.exists():
                        bak.unlink()
                    del self._backups[orig]
                    return
            raise FileNotFoundError(f"No backup found for: {original_path}")

    def list_backups(self) -> list[BackupRecord]:
        return list(self._backups.values())


# -- SQLite database write --


def chapters_to_json(chapters: list[Chapter]) -> str:
    """Serialise chapters to the JSON format AudioSilo expects for scanned_chapters."""
    entries = []
    for i, c in enumerate(chapters):
        entries.append(
            {
                "index": i,
                "title": c.title,
                "scanned_title": c.title,
                "edited": False,
                "start": c.start_seconds,
                "end": c.end_seconds,
                "book_offset": c.start_seconds,
            }
        )
    payload = {
        "version": 1,
        "generator": "chapter-sidecar",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": [],
        "chapters": entries,
    }
    return json.dumps(payload, ensure_ascii=False)


def write_chapters_to_db(
    db_path: str,
    library_id: int,
    book_path: str,
    chapters: list[Chapter],
    dry_run: bool = False,
) -> dict:
    """Write chapters directly to the AudioSilo SQLite database.

    Updates the `books` table:
      - scanned_chapters: JSON array of chapter entries
      - chapters_source: '' (empty string = use files/scan chapters)

    Creates a backup before writing unless dry_run is True.
    Returns a dict with backup info and write result.
    """
    if dry_run:
        return {
            "method": "db",
            "dry_run": True,
            "book_path": book_path,
            "chapter_count": len(chapters),
            "message": "Dry run: no database changes made",
        }

    writer = DirectWriter(DirectWriteConfig(backup_dir=""))

    # Step 1: Backup
    backup_record = writer.backup_database(db_path)

    # Step 2: Connect and write
    chapters_json = chapters_to_json(chapters)
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row

        # Find the book
        row = conn.execute(
            "SELECT id, library_id, rel_path, title, chapters_source, scanned_chapters "
            "FROM books WHERE library_id = ? AND rel_path = ?",
            (library_id, book_path),
        ).fetchone()

        if row is None:
            writer.restore_backup(db_path)
            raise ValueError(
                f"Book not found in database: library_id={library_id}, path={book_path}"
            )

        # Step 3: Update
        conn.execute(
            "UPDATE books SET scanned_chapters = ?, chapters_source = '' "
            "WHERE library_id = ? AND rel_path = ?",
            (chapters_json, library_id, book_path),
        )
        conn.commit()

        # Step 4: Verify
        verify = conn.execute(
            "SELECT scanned_chapters, chapters_source FROM books "
            "WHERE library_id = ? AND rel_path = ?",
            (library_id, book_path),
        ).fetchone()

        return {
            "method": "db",
            "dry_run": False,
            "book_path": book_path,
            "chapter_count": len(chapters),
            "backup_path": backup_record.backup_path,
            "backup_created_at": backup_record.created_at,
            "verified": verify["scanned_chapters"] is not None
            and verify["chapters_source"] == "",
            "message": f"Wrote {len(chapters)} chapters to database. "
            f"Backup at: {backup_record.backup_path}",
        }
    finally:
        conn.close()


def write_chapters_to_audiofile(
    file_path: str,
    chapters: list[Chapter],
    dry_run: bool = False,
) -> dict:
    """Write chapters directly to an m4b audio file as MP4 chapters.

    Requires the `mutagen` library. Creates a backup before writing.
    """
    if dry_run:
        return {
            "method": "audiofile",
            "dry_run": True,
            "file_path": file_path,
            "chapter_count": len(chapters),
            "message": "Dry run: no audio file changes made",
        }

    try:
        from mutagen.mp4 import MP4, MP4Chap
    except ImportError:
        return {
            "method": "audiofile",
            "dry_run": False,
            "error": "mutagen library not installed. "
            "Install with: pip install mutagen",
        }

    writer = DirectWriter(DirectWriteConfig(backup_dir=""))

    # Step 1: Backup
    backup_record = writer.backup_audiofile(file_path)

    try:
        audio = MP4(file_path)

        # Build MP4 chapter atoms
        chapters_list = []
        for c in chapters:
            # MP4 chapters use milliseconds
            start_ms = int(c.start_seconds * 1000)
            chapters_list.append((start_ms, c.title))

        audio["chap"] = MP4Chap(chapters_list)
        audio.save()

        return {
            "method": "audiofile",
            "dry_run": False,
            "file_path": file_path,
            "chapter_count": len(chapters),
            "backup_path": backup_record.backup_path,
            "backup_created_at": backup_record.created_at,
            "verified": True,
            "message": f"Wrote {len(chapters)} chapters to audio file. "
            f"Backup at: {backup_record.backup_path}",
        }
    except Exception as exc:
        # Try to restore on failure
        try:
            writer.restore_backup(file_path)
        except Exception:
            pass
        return {
            "method": "audiofile",
            "dry_run": False,
            "error": str(exc),
            "backup_path": backup_record.backup_path,
            "message": f"Write failed: {exc}. Backup preserved at: {backup_record.backup_path}",
        }


__all__ = [
    "DirectWriteConfig",
    "DirectWriter",
    "BackupRecord",
    "write_chapters_to_db",
    "write_chapters_to_audiofile",
    "chapters_to_json",
]