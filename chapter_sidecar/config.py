"""Configuration loader for the chapter sidecar.

Loads settings from .env and environment variables. .env is never committed.
All values have sensible defaults; required values (API token, library ID)
must be supplied in .env before the sidecar can connect to AudioSilo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path | str) -> None:
    """Minimal .env loader (no external dependency).

    Reads KEY=value lines, strips inline comments after whitespace,
    ignores blank lines and lines starting with #. Does not override
    variables already set in the environment.
    """
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Remove surrounding quotes if present
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        # Only set if not already in environment
        if key and key not in os.environ:
            os.environ[key] = value


# Load .env from the project root (two levels up from this file)
_load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@dataclass(frozen=True)
class Config:
    audiosilo_base_url: str
    audiosilo_api_token: str | None
    audiosilo_library_id: str | None
    sidecar_database: str
    audiosilo_request_timeout: int
    provider_request_timeout: int
    community_poll_interval: int
    community_poll_timeout: int
    max_concurrent_books: int
    request_delay_seconds: float
    duration_tolerance_seconds: float
    audnexus_region: str
    dry_run: bool
    dry_run_output_dir: str
    # Direct write (testing only - bypasses API)
    direct_write_enabled: bool
    direct_write_method: str  # "db" | "audiofile"
    audiosilo_db_path: str  # Path to AudioSilo SQLite database
    direct_write_backup_dir: str  # Directory for backups

    @classmethod
    def from_env(cls) -> "Config":
        def _env(name: str, default: str) -> str:
            return os.environ.get(name, default)

        def _env_int(name: str, default: int) -> int:
            try:
                return int(os.environ.get(name, str(default)))
            except (TypeError, ValueError):
                return default

        def _env_float(name: str, default: float) -> float:
            try:
                return float(os.environ.get(name, str(default)))
            except (TypeError, ValueError):
                return default

        def _env_bool(name: str, default: bool) -> bool:
            return os.environ.get(name, str(default)).strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )

        return cls(
            audiosilo_base_url=_env("AUDIOSILO_BASE_URL", "http://192.168.68.102:8610").rstrip("/"),
            audiosilo_api_token=_env("AUDIOSILO_API_TOKEN", "") or None,
            audiosilo_library_id=_env("AUDIOSILO_LIBRARY_ID", "") or None,
            sidecar_database=_env("SIDECAR_DATABASE", "./data/chapter_sidecar.sqlite3"),
            audiosilo_request_timeout=_env_int("AUDIOSILO_REQUEST_TIMEOUT", 20),
            provider_request_timeout=_env_int("PROVIDER_REQUEST_TIMEOUT", 20),
            community_poll_interval=_env_int("COMMUNITY_POLL_INTERVAL", 5),
            community_poll_timeout=_env_int("COMMUNITY_POLL_TIMEOUT", 180),
            max_concurrent_books=_env_int("MAX_CONCURRENT_BOOKS", 1),
            request_delay_seconds=_env_float("REQUEST_DELAY_SECONDS", 1.0),
            duration_tolerance_seconds=_env_float("DURATION_TOLERANCE_SECONDS", 5.0),
            audnexus_region=_env("AUDNEXUS_REGION", "US"),
            dry_run=_env_bool("DRY_RUN", True),
            dry_run_output_dir=_env("DRY_RUN_OUTPUT_DIR", "./data/dryrun"),
            # Direct write (testing only)
            direct_write_enabled=_env_bool("DIRECT_WRITE_ENABLED", False),
            direct_write_method=_env("DIRECT_WRITE_METHOD", "db"),
            audiosilo_db_path=_env("AUDIOSILO_DB_PATH", ""),
            direct_write_backup_dir=_env("DIRECT_WRITE_BACKUP_DIR", "./data/backups"),
        )


def load_config() -> Config:
    """Load configuration from .env and environment variables."""
    return Config.from_env()