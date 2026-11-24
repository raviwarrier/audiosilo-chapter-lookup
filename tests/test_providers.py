"""Tests for provider adapters (with mocked responses)."""

from unittest.mock import patch

import pytest

from chapter_sidecar.providers.audnexus import AudnexusProvider
from chapter_sidecar.providers.base import RecordingMatch


def make_recording(asin="B000000001", title="Test Book"):
    return RecordingMatch(
        provider="audnexus",
        recording_id=asin,
        title=title,
        author="Test Author",
        duration_seconds=None,
        raw={"asin": asin},
    )


def test_audnexus_search_returns_asin_match():
    provider = AudnexusProvider(region="US")
    matches = provider.search(title="Test Book", author="Test Author", isbn=None, asin="B000000001")
    assert len(matches) == 1
    assert matches[0].recording_id == "B000000001"


def test_audnexus_search_no_asin_returns_empty():
    provider = AudnexusProvider(region="US")
    matches = provider.search(title="Test Book", author="Test Author", isbn=None, asin=None)
    assert matches == []


@patch("chapter_sidecar.providers.audnexus.requests.get")
def test_audnexus_get_chapters_success(mock_get):
    mock_response = mock_get.return_value
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "chapters": [
            {"startOffsetMs": 0, "lengthMs": 60000, "title": "Chapter 1"},
            {"startOffsetMs": 60000, "lengthMs": 60000, "title": "Chapter 2"},
        ]
    }
    mock_response.headers = {}

    provider = AudnexusProvider(region="US")
    result = provider.get_chapters(make_recording())

    assert result.kind == "success"
    assert result.chapters is not None
    assert len(result.chapters) == 2
    assert result.chapters[0].start_seconds == 0.0
    assert result.chapters[0].end_seconds == 60.0


@patch("chapter_sidecar.providers.audnexus.requests.get")
def test_audnexus_get_chapters_rate_limited(mock_get):
    mock_response = mock_get.return_value
    mock_response.status_code = 429
    mock_response.headers = {"Retry-After": "5"}

    provider = AudnexusProvider(region="US")
    result = provider.get_chapters(make_recording())

    assert result.kind == "temporary_error"
    assert result.error_code == "rate_limited"
    assert result.retry_after == 5.0


@patch("chapter_sidecar.providers.audnexus.requests.get")
def test_audnexus_get_chapters_not_found(mock_get):
    mock_response = mock_get.return_value
    mock_response.status_code = 404
    mock_response.headers = {}

    provider = AudnexusProvider(region="US")
    result = provider.get_chapters(make_recording())

    assert result.kind == "no_chapters"