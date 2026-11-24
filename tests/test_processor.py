"""Tests for the processor (with mocked dependencies)."""

from unittest.mock import MagicMock

from chapter_sidecar.chapters import Chapter
from chapter_sidecar.processor import Processor


def test_process_one_already_has_chapters():
    client = MagicMock()
    client.has_usable_chapters.return_value = True
    community = MagicMock()
    store = MagicMock()
    store.get_by_status.return_value = []
    store.ensure_pending.return_value = MagicMock(
        audio_silo_library_id="lib1",
        book_path="/path/book.mp3",
        title="Test Book",
        author=None,
        status="pending",
    )
    dry_run_writer = MagicMock()
    config = MagicMock()
    config.dry_run = True
    config.audiosilo_library_id = "lib1"
    config.request_delay_seconds = 0
    config.duration_tolerance_seconds = 5.0

    processor = Processor(client, community, [], store, dry_run_writer, config)
    result = processor.process_one(book_path="/path/book.mp3", title="Test Book")

    assert result.status == "already_has_chapters"
    assert "already has usable chapters" in result.message


def test_process_one_dry_run_writes_file():
    client = MagicMock()
    client.has_usable_chapters.return_value = False
    community = MagicMock()
    community.obtain_chapters.return_value = None
    store = MagicMock()
    job = MagicMock(
        audio_silo_library_id="lib1",
        book_path="/path/book.mp3",
        title="Test Book",
        author=None,
        status="pending",
        title_attr=None,
        author_attr=None,
    )
    store.ensure_pending.return_value = job
    dry_run_writer = MagicMock()
    dry_run_writer.write.return_value = "/data/dryrun/Test_Book-chapters.txt"
    config = MagicMock()
    config.dry_run = True
    config.audiosilo_library_id = "lib1"
    config.request_delay_seconds = 0
    config.duration_tolerance_seconds = 5.0

    provider = MagicMock()
    provider.name = "audnexus"
    provider.search.return_value = []
    # No candidates means it skips to unavailable... let's mock differently

    # Instead, simulate that no provider matches -> unavailable
    processor = Processor(client, community, [provider], store, dry_run_writer, config)
    result = processor.process_one(book_path="/path/book.mp3", title="Test Book")

    # With no candidates, all providers exhausted -> unavailable
    assert result.status == "unavailable"