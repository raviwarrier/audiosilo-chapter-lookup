"""Provider-independent interface for chapter lookup adapters.

Provider-specific response parsing belongs inside each adapter. Do not leak
Audnexus or AudioSilo Meta response fields into the processor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class RecordingMatch:
    provider: str
    recording_id: str
    title: str
    author: str | None
    duration_seconds: float | None
    raw: dict


@dataclass
class Chapter:
    title: str
    start_seconds: float
    end_seconds: float


@dataclass
class LookupResult:
    """Outcome of a provider lookup for a book."""

    kind: str  # "success" | "no_match" | "no_chapters" | "ambiguous" | "temporary_error" | "unexpected"
    chapters: list[Chapter] | None = None
    recording: RecordingMatch | None = None
    recording_id: str | None = None
    error: str | None = None
    error_code: str | None = None
    retry_after: float | None = None


class ChapterProvider(Protocol):
    name: str

    def search(
        self,
        *,
        title: str,
        author: str | None,
        isbn: str | None,
        asin: str | None,
    ) -> list[RecordingMatch]:
        """Search for recordings matching the given identifiers."""
        ...

    def get_chapters(
        self,
        recording: RecordingMatch,
    ) -> LookupResult:
        """Retrieve chapters for a specific recording."""
        ...