"""Tests for chapter data model, conversion, and validation."""

import pytest

from chapter_sidecar.chapters import Chapter, chapters_from_offsets, milliseconds_to_seconds, validate_chapters


def test_milliseconds_to_seconds():
    assert milliseconds_to_seconds(1000) == 1.0
    assert milliseconds_to_seconds(0) == 0.0
    assert milliseconds_to_seconds(1500) == 1.5


def test_chapters_from_offsets():
    items = [
        {"startOffsetMs": 0, "lengthMs": 60000, "title": "Chapter 1"},
        {"startOffsetMs": 60000, "lengthMs": 60000, "title": "Chapter 2"},
    ]
    chapters = chapters_from_offsets(items)
    assert len(chapters) == 2
    assert chapters[0] == Chapter(title="Chapter 1", start_seconds=0.0, end_seconds=60.0)
    assert chapters[1] == Chapter(title="Chapter 2", start_seconds=60.0, end_seconds=120.0)


def test_validate_chapters_valid():
    chapters = [
        Chapter(title="C1", start_seconds=0.0, end_seconds=60.0),
        Chapter(title="C2", start_seconds=60.0, end_seconds=120.0),
    ]
    result = validate_chapters(chapters, audiobook_duration=120.0)
    assert len(result) == 2


def test_validate_chapters_empty():
    with pytest.raises(ValueError, match="no chapters"):
        validate_chapters([], audiobook_duration=120.0)


def test_validate_chapters_negative_start():
    chapters = [Chapter(title="C1", start_seconds=-1.0, end_seconds=10.0)]
    with pytest.raises(ValueError, match="negative"):
        validate_chapters(chapters, audiobook_duration=120.0)


def test_validate_chapters_end_before_start():
    chapters = [Chapter(title="C1", start_seconds=60.0, end_seconds=30.0)]
    with pytest.raises(ValueError, match="end must follow"):
        validate_chapters(chapters, audiobook_duration=120.0)


def test_validate_chapters_exceeds_duration():
    chapters = [Chapter(title="C1", start_seconds=0.0, end_seconds=200.0)]
    # End time is capped at audiobook duration instead of rejected
    result = validate_chapters(chapters, audiobook_duration=120.0)
    assert result[0].end_seconds == 120.0


def test_validate_chapters_start_beyond_duration():
    chapters = [Chapter(title="C1", start_seconds=150.0, end_seconds=200.0)]
    with pytest.raises(ValueError, match="at or beyond"):
        validate_chapters(chapters, audiobook_duration=120.0)


def test_validate_chapters_overlap():
    chapters = [
        Chapter(title="C1", start_seconds=0.0, end_seconds=60.0),
        Chapter(title="C2", start_seconds=30.0, end_seconds=120.0),
    ]
    with pytest.raises(ValueError, match="overlap"):
        validate_chapters(chapters, audiobook_duration=120.0)


def test_validate_chapters_fallback_title():
    chapters = [Chapter(title="", start_seconds=0.0, end_seconds=60.0)]
    result = validate_chapters(chapters, audiobook_duration=120.0)
    assert result[0].title == "Chapter 1"