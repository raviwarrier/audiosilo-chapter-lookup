"""Chapter data model, conversion, and validation.

One internal chapter representation independent of any provider or AudioSilo
schema. Providers convert their own timestamp formats into this model in
their adapters. The rest of the program uses seconds consistently.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Chapter:
    """A single chapter boundary in seconds."""

    title: str
    start_seconds: float
    end_seconds: float


def milliseconds_to_seconds(value: int | float) -> float:
    """Convert a provider timestamp from milliseconds to seconds."""
    result = float(value) / 1000.0
    if not math.isfinite(result):
        raise ValueError("Timestamp is not finite")
    return result


def chapters_from_offsets(items: list[dict]) -> list[Chapter]:
    """Convert a provider list of {startOffsetMs, lengthMs, title} items.

    Example adapter for Audnexus-style responses. Confirm the real provider
    schema before using.
    """
    chapters: list[Chapter] = []

    for item in items:
        start = milliseconds_to_seconds(item["startOffsetMs"])
        duration = milliseconds_to_seconds(item["lengthMs"])

        chapters.append(
            Chapter(
                title=(item.get("title") or "").strip(),
                start_seconds=start,
                end_seconds=start + duration,
            )
        )

    return chapters


def validate_chapters(
    chapters: list[Chapter],
    audiobook_duration: float,
    tolerance: float = 5.0,
) -> list[Chapter]:
    """Reject unsafe or implausible chapter timelines.

    Returns a new list of validated chapters. Chapter end times that
    slightly exceed the audiobook duration are capped at the duration
    (consistent with Audiobookshelf behaviour), rather than rejected
    outright.
    """
    if not chapters:
        raise ValueError("Provider returned no chapters")

    if audiobook_duration <= 0:
        raise ValueError("Audiobook duration is invalid")

    ordered = sorted(chapters, key=lambda item: item.start_seconds)

    validated: list[Chapter] = []

    for index, chapter in enumerate(ordered):
        title = chapter.title or f"Chapter {index + 1}"

        if not math.isfinite(chapter.start_seconds):
            raise ValueError(f"Chapter {index + 1}: start is not finite")
        if not math.isfinite(chapter.end_seconds):
            raise ValueError(f"Chapter {index + 1}: end is not finite")
        if chapter.start_seconds < 0:
            raise ValueError(f"Chapter {index + 1}: negative start")
        if chapter.end_seconds <= chapter.start_seconds:
            raise ValueError(f"Chapter {index + 1}: end must follow start")
        # Cap end time at audiobook duration (Audiobookshelf behaviour).
        # Chapters starting beyond the duration are rejected.
        if chapter.start_seconds >= audiobook_duration:
            raise ValueError(
                f"Chapter {index + 1} start ({chapter.start_seconds}s) is at "
                f"or beyond audiobook duration ({audiobook_duration}s)"
            )
        end_seconds = min(chapter.end_seconds, audiobook_duration)
        if end_seconds <= chapter.start_seconds:
            raise ValueError(
                f"Chapter {index + 1}: end must follow start after capping"
            )

        if validated:
            previous = validated[-1]
            if chapter.start_seconds < previous.end_seconds - 0.5:
                raise ValueError(
                    f"Chapter {index + 1} overlaps previous chapter by more "
                    "than allowed tolerance"
                )

        validated.append(
            Chapter(
                title=title,
                start_seconds=chapter.start_seconds,
                end_seconds=end_seconds,
            )
        )

    return validated