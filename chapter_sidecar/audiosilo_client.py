"""AudioSilo API client.

WARNING: The endpoints and field names below are CANDIDATES from the
implementation brief. They MUST be verified against the running AudioSilo
2.6.0 instance before any chapter writes are enabled. Do not assume they
are correct for 2.6.0.

Candidate endpoints:
- GET  /api/v1/admin/libraries/{library_id}/books          (list books)
- GET  /api/v1/admin/libraries/{library_id}/book?path=...  (fetch one book)
- POST /api/v1/admin/libraries/{library_id}/book/community-chapters?path=...
- PUT  /api/v1/admin/libraries/{library_id}/book/chapters?path=...

Authentication method is unknown; the spec says do not assume bearer token
until verified. The client supports a configurable header name/value.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import requests

from .chapters import Chapter
from .config import Config
from .direct_write import write_chapters_to_db, write_chapters_to_audiofile


class AudioSiloClient:
    def __init__(self, config: Config):
        self.config = config
        self.base_url = config.audiosilo_base_url.rstrip("/")
        self.token = config.audiosilo_api_token
        self.library_id = config.audiosilo_library_id
        self.timeout = config.audiosilo_request_timeout
        self._session = requests.Session()
        if self.token:
            # Default to Authorization: Bearer, but this must be verified.
            # Adjust header name/value once the auth method is confirmed.
            self._session.headers.update(
                {"Authorization": f"Bearer {self.token}"}
            )
        self._session.headers.update(
            {"Accept": "application/json", "Content-Type": "application/json"}
        )

    # -- low-level --

    def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        response = self._session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def _post(self, path: str, params: dict | None = None, json_body: dict | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        response = self._session.post(
            url, params=params, json=json_body, timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()

    def _put(self, path: str, params: dict | None = None, json_body: dict | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        response = self._session.put(
            url, params=params, json=json_body, timeout=self.timeout
        )
        response.raise_for_status()
        return response.json()

    # -- library / book operations --

    def list_libraries(self) -> list[dict[str, Any]]:
        """List AudioSilo libraries. Candidate endpoint; verify path."""
        return self._get("/api/v1/admin/libraries")

    def list_books(self) -> list[dict[str, Any]]:
        """List books in the configured library.

        Verified endpoint: GET /api/v1/admin/books?library_id={id}
        Returns paginated results with 'books' array and 'next_cursor'.
        This method returns the first page only.
        """
        if not self.library_id:
            raise ValueError("AUDIOSILO_LIBRARY_ID is not set")
        return self._get(
            "/api/v1/admin/books",
            params={"library_id": self.library_id},
        )

    def list_books_all(self) -> list[dict[str, Any]]:
        """List all books by paginating through the cursor-based API."""
        if not self.library_id:
            raise ValueError("AUDIOSILO_LIBRARY_ID is not set")

        all_books: list[dict[str, Any]] = []
        cursor: str | None = None

        while True:
            params: dict[str, str] = {"library_id": self.library_id}
            if cursor:
                params["cursor"] = cursor

            data = self._get("/api/v1/admin/books", params=params)
            books = data.get("books", [])
            all_books.extend(books)

            cursor = data.get("next_cursor")
            if not cursor:
                break

        return all_books

    def get_book(self, book_path: str) -> dict[str, Any]:
        """Fetch a single book record by path.

        Verified endpoint: GET /api/v1/admin/libraries/{library_id}/book?path=...
        Response wraps the book record in a "book" key; this method unwraps it.
        """
        if not self.library_id:
            raise ValueError("AUDIOSILO_LIBRARY_ID is not set")
        response = self._get(
            f"/api/v1/admin/libraries/{self.library_id}/book",
            params={"path": book_path},
        )
        return response.get("book", response)

    # -- chapter status --

    def has_usable_chapters(self, book: dict[str, Any]) -> bool:
        """Determine whether the book already has usable chapters.

        Based on observed API: chapter_count > 1 means usable chapters exist.
        A book with chapter_count <= 1 effectively has no chapters.
        chapters_source indicates where chapters came from (files, community, etc.).
        """
        chapter_count = book.get("chapter_count", 0) or 0
        return chapter_count > 1

    def community_check_state(self, book: dict[str, Any]) -> dict[str, Any]:
        """Extract community check status fields from a book response.

        Based on observed API: chapters_check field indicates status:
        - "unavailable": no community chapters available
        - "fill": community chapters available
        - "no_match": no match found
        - "refine": needs refinement
        """
        chapters_check = book.get("chapters_check")
        return {
            "checking": False,
            "checked": chapters_check is not None,
            "has_community_chapters": chapters_check == "fill",
            "community_error": None,
            "chapters_check": chapters_check,
        }

    # -- community chapter lookup --

    def start_community_check(self, book_path: str) -> dict[str, Any]:
        """Trigger AudioSilo's community chapter check. Candidate endpoint."""
        if not self.library_id:
            raise ValueError("AUDIOSILO_LIBRARY_ID is not set")
        return self._post(
            f"/api/v1/admin/libraries/{self.library_id}/book/community-chapters",
            params={"path": book_path},
        )

    # -- chapter write --

    def write_chapters(
        self,
        book_path: str,
        chapters: list[Chapter],
    ) -> dict[str, Any]:
        """Write chapters to AudioSilo.

        If DIRECT_WRITE_ENABLED=true in .env, writes directly to the
        SQLite database or audio file (testing only, with backup).
        Otherwise uses the verified PUT /book/chapters endpoint (not yet
        available in AudioSilo 2.6.0).
        """
        # Direct write mode for testing (bypasses API)
        if self.config.direct_write_enabled:
            return self._write_chapters_direct(book_path, chapters)

        # Verified endpoint: PUT /api/v1/admin/libraries/{library_id}/book/chapters?path={book_path}
        # Not yet available in AudioSilo 2.6.0 (exists only in unreleased main).
        raise NotImplementedError(
            "write_chapters via API is not available in AudioSilo 2.6.0. "
            "Set DIRECT_WRITE_ENABLED=true in .env for direct database/audiofile "
            "writing during testing, or wait for the server to be upgraded."
        )

    def _write_chapters_direct(
        self,
        book_path: str,
        chapters: list[Chapter],
    ) -> dict[str, Any]:
        """Write chapters directly to the database or audio file."""
        method = self.config.direct_write_method

        if method == "db":
            db_path = self.config.audiosilo_db_path
            if not db_path:
                return {
                    "method": "db",
                    "dry_run": False,
                    "error": "AUDIOSILO_DB_PATH is not set in .env",
                    "message": "Set AUDIOSILO_DB_PATH to your AudioSilo SQLite database path",
                }
            library_id = int(self.config.audiosilo_library_id) if self.config.audiosilo_library_id else 0
            return write_chapters_to_db(
                db_path=db_path,
                library_id=library_id,
                book_path=book_path,
                chapters=chapters,
                dry_run=self.config.dry_run,
            )

        if method == "audiofile":
            file_path = self.config.audiosilo_db_path  # reuse db_path field for audiofile path
            if not file_path:
                return {
                    "method": "audiofile",
                    "dry_run": False,
                    "error": "AUDIOSILO_DB_PATH is not set in .env (used as audio file path for audiofile method)",
                    "message": "Set AUDIOSILO_DB_PATH to the full path of the .m4b file",
                }
            return write_chapters_to_audiofile(
                file_path=file_path,
                chapters=chapters,
                dry_run=self.config.dry_run,
            )

        return {
            "method": method,
            "dry_run": False,
            "error": f"Unknown direct_write_method: {method}",
            "message": "Set DIRECT_WRITE_METHOD to 'db' or 'audiofile'",
        }

    @staticmethod
    def payload_hash(chapters: list[Chapter]) -> str:
        """Stable hash of a chapter payload for change detection."""
        serialised = json.dumps(
            [
                {"title": c.title, "start": c.start_seconds, "end": c.end_seconds}
                for c in chapters
            ],
            sort_keys=True,
        )
        return hashlib.sha256(serialised.encode("utf-8")).hexdigest()

    def extract_duration(self, book: dict[str, Any]) -> float | None:
        """Extract audiobook duration in seconds from a book response.

        Verified field: book["duration"] is in seconds (float).
        """
        duration = book.get("duration")
        if duration is None:
            return None
        try:
            return float(duration)
        except (TypeError, ValueError):
            return None