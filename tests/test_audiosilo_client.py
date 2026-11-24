"""Tests for the AudioSilo client (stub behaviour)."""

from chapter_sidecar.audiosilo_client import AudioSiloClient
from chapter_sidecar.config import Config


def test_client_has_usable_chapters_defaults_false():
    config = Config(
        audiosilo_base_url="http://localhost:8610",
        audiosilo_api_token=None,
        audiosilo_library_id="lib1",
        sidecar_database="./data/test.sqlite3",
        audiosilo_request_timeout=20,
        provider_request_timeout=20,
        community_poll_interval=5,
        community_poll_timeout=180,
        max_concurrent_books=1,
        request_delay_seconds=1.0,
        duration_tolerance_seconds=5.0,
        audnexus_region="US",
        dry_run=True,
        dry_run_output_dir="./data/dryrun",
        direct_write_enabled=False,
        direct_write_method="db",
        audiosilo_db_path="",
        direct_write_backup_dir="./data/backups",
    )
    client = AudioSiloClient(config)
    assert client.has_usable_chapters({}) is False


def test_client_payload_hash_is_stable():
    config = Config(
        audiosilo_base_url="http://localhost:8610",
        audiosilo_api_token=None,
        audiosilo_library_id="lib1",
        sidecar_database="./data/test.sqlite3",
        audiosilo_request_timeout=20,
        provider_request_timeout=20,
        community_poll_interval=5,
        community_poll_timeout=180,
        max_concurrent_books=1,
        request_delay_seconds=1.0,
        duration_tolerance_seconds=5.0,
        audnexus_region="US",
        dry_run=True,
        dry_run_output_dir="./data/dryrun",
        direct_write_enabled=False,
        direct_write_method="db",
        audiosilo_db_path="",
        direct_write_backup_dir="./data/backups",
    )
    client = AudioSiloClient(config)
    chapters = [
        __import__("chapter_sidecar.chapters", fromlist=["Chapter"]).Chapter(
            title="C1", start_seconds=0.0, end_seconds=60.0
        ),
    ]
    h1 = client.payload_hash(chapters)
    h2 = client.payload_hash(chapters)
    assert h1 == h2
    assert len(h1) == 64  # SHA256 hex digest