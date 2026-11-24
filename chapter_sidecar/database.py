"""Sidecar state database (SQLite).

Separate from AudioSilo's own database. Tracks book processing status,
retry information, and provenance. Never modifies AudioSilo's database.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class BookJob:
    audio_silo_library_id: str
    book_path: str
    book_id: Optional[str] = None
    title: Optional[str] = None
    author: Optional[str] = None
    status: str = "pending"
    community_status: Optional[str] = None
    external_provider: Optional[str] = None
    external_recording_id: Optional[str] = None
    chapter_count: Optional[int] = None
    last_attempt_at: Optional[str] = None
    completed_at: Optional[str] = None
    retry_after: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    source_payload_hash: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS book_jobs (
    audio_silo_library_id TEXT NOT NULL,
    book_path TEXT NOT NULL,
    book_id TEXT,
    title TEXT,
    author TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    community_status TEXT,
    external_provider TEXT,
    external_recording_id TEXT,
    chapter_count INTEGER,
    last_attempt_at TEXT,
    completed_at TEXT,
    retry_after TEXT,
    error_code TEXT,
    error_message TEXT,
    source_payload_hash TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (audio_silo_library_id, book_path)
);
"""

INSERT_SQL = """
INSERT OR IGNORE INTO book_jobs (
    audio_silo_library_id, book_path, book_id, title, author, status,
    created_at, updated_at
) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
"""

UPSERT_SQL = """
INSERT INTO book_jobs (
    audio_silo_library_id, book_path, book_id, title, author, status,
    community_status, external_provider, external_recording_id,
    chapter_count, last_attempt_at, completed_at, retry_after,
    error_code, error_message, source_payload_hash, created_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(audio_silo_library_id, book_path) DO UPDATE SET
    book_id = excluded.book_id,
    title = excluded.title,
    author = excluded.author,
    status = excluded.status,
    community_status = excluded.community_status,
    external_provider = excluded.external_provider,
    external_recording_id = excluded.external_recording_id,
    chapter_count = excluded.chapter_count,
    last_attempt_at = excluded.last_attempt_at,
    completed_at = excluded.completed_at,
    retry_after = excluded.retry_after,
    error_code = excluded.error_code,
    error_message = excluded.error_message,
    source_payload_hash = excluded.source_payload_hash,
    updated_at = excluded.updated_at
"""

SELECT_BY_PATH_SQL = """
SELECT * FROM book_jobs
WHERE audio_silo_library_id = ? AND book_path = ?
"""

SELECT_BY_STATUS_SQL = """
SELECT * FROM book_jobs
WHERE audio_silo_library_id = ? AND status = ?
ORDER BY updated_at
"""

SELECT_ALL_SQL = """
SELECT * FROM book_jobs
WHERE audio_silo_library_id = ?
ORDER BY updated_at
"""


class SidecarDatabase:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.database_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(CREATE_TABLE_SQL)
            conn.commit()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _row_to_job(self, row: sqlite3.Row) -> BookJob:
        return BookJob(
            audio_silo_library_id=row["audio_silo_library_id"],
            book_path=row["book_path"],
            book_id=row["book_id"],
            title=row["title"],
            author=row["author"],
            status=row["status"],
            community_status=row["community_status"],
            external_provider=row["external_provider"],
            external_recording_id=row["external_recording_id"],
            chapter_count=row["chapter_count"],
            last_attempt_at=row["last_attempt_at"],
            completed_at=row["completed_at"],
            retry_after=row["retry_after"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            source_payload_hash=row["source_payload_hash"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def upsert(self, job: BookJob) -> None:
        now = self._now()
        created_at = job.created_at or now
        with self._connect() as conn:
            conn.execute(
                UPSERT_SQL,
                (
                    job.audio_silo_library_id,
                    job.book_path,
                    job.book_id,
                    job.title,
                    job.author,
                    job.status,
                    job.community_status,
                    job.external_provider,
                    job.external_recording_id,
                    job.chapter_count,
                    job.last_attempt_at,
                    job.completed_at,
                    job.retry_after,
                    job.error_code,
                    job.error_message,
                    job.source_payload_hash,
                    created_at,
                    now,
                ),
            )
            conn.commit()

    def get_by_path(
        self, library_id: str, book_path: str
    ) -> Optional[BookJob]:
        with self._connect() as conn:
            row = conn.execute(
                SELECT_BY_PATH_SQL, (library_id, book_path)
            ).fetchone()
        return self._row_to_job(row) if row else None

    def get_by_status(
        self, library_id: str, status: str
    ) -> list[BookJob]:
        with self._connect() as conn:
            rows = conn.execute(
                SELECT_BY_STATUS_SQL, (library_id, status)
            ).fetchall()
        return [self._row_to_job(row) for row in rows]

    def get_all(self, library_id: str) -> list[BookJob]:
        with self._connect() as conn:
            rows = conn.execute(SELECT_ALL_SQL, (library_id,)).fetchall()
        return [self._row_to_job(row) for row in rows]

    def ensure_pending(self, library_id: str, book_path: str) -> BookJob:
        """Ensure a book job exists with pending status, returning it."""
        existing = self.get_by_path(library_id, book_path)
        if existing:
            return existing
        now = self._now()
        job = BookJob(
            audio_silo_library_id=library_id,
            book_path=book_path,
            status="pending",
            created_at=now,
            updated_at=now,
        )
        self.upsert(job)
        return job

    def count_by_status(self, library_id: str) -> dict[str, int]:
        jobs = self.get_all(library_id)
        counts: dict[str, int] = {}
        for job in jobs:
            counts[job.status] = counts.get(job.status, 0) + 1
        return counts