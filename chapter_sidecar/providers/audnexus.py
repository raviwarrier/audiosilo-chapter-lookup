"""Audnexus chapter provider adapter.

Candidate endpoint (must be verified against the live service):
    GET https://api.audnex.us/books/{URL-encoded-uppercase-ASIN}/chapters?region={region}

Follows the Audiobookshelf Audnexus implementation pattern (pinned commit
5de038dafda294d13ca48861eda7d4405bbd7368):
- ASIN is required, uppercased and URL-encoded
- Region query parameter (default us; this installation defaults to US)
- Throttling: one request every 150 ms
- Retries HTTP 429 using Retry-After header, 5s fallback
- Returns the Audnexus response chapter data unchanged
"""

from __future__ import annotations

import time
import urllib.parse
from typing import Any

import requests

from .base import Chapter, LookupResult, RecordingMatch


class AudnexusProvider:
    name = "audnexus"

    def __init__(
        self,
        *,
        region: str = "US",
        base_url: str = "https://api.audnex.us",
        timeout: int = 20,
        min_interval_seconds: float = 0.15,
    ):
        self.region = region
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.min_interval_seconds = min_interval_seconds
        self._last_request_time: float = 0.0

    # -- search interface --

    def search(
        self,
        *,
        title: str,
        author: str | None,
        isbn: str | None,
        asin: str | None,
    ) -> list[RecordingMatch]:
        """Audnexus does not expose a title/search endpoint. Returns matches
        only when a direct ASIN is supplied (used as a recording identity
        hint for get_chapters)."""
        matches: list[RecordingMatch] = []
        if asin:
            matches.append(
                RecordingMatch(
                    provider=self.name,
                    recording_id=asin.upper(),
                    title=title,
                    author=author,
                    duration_seconds=None,
                    raw={"asin": asin.upper()},
                )
            )
        return matches

    def get_chapters(
        self,
        recording: RecordingMatch,
    ) -> LookupResult:
        asin = recording.recording_id
        if not asin or len(asin) != 10:
            return LookupResult(
                kind="unexpected",
                error=f"Invalid ASIN for Audnexus lookup: {asin}",
            )

        # Throttle: enforce minimum interval between requests
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self.min_interval_seconds:
            time.sleep(self.min_interval_seconds - elapsed)

        url = f"{self.base_url}/books/{urllib.parse.quote(asin.upper())}/chapters"
        params: dict[str, str] = {}
        if self.region:
            # Audiobookshelf converts region to lowercase before sending
            params["region"] = self.region.lower()

        try:
            self._last_request_time = time.monotonic()
            response = requests.get(url, params=params, timeout=self.timeout)
        except requests.exceptions.Timeout:
            return LookupResult(
                kind="temporary_error",
                error="Audnexus request timed out",
                error_code="timeout",
            )
        except requests.exceptions.RequestException as exc:
            return LookupResult(
                kind="temporary_error",
                error=f"Audnexus request failed: {exc}",
                error_code="network_error",
            )

        if response.status_code == 429:
            retry_after = None
            retry_header = response.headers.get("Retry-After")
            if retry_header:
                try:
                    retry_after = float(retry_header)
                except ValueError:
                    retry_after = 5.0
            else:
                retry_after = 5.0
            return LookupResult(
                kind="temporary_error",
                error="Audnexus rate limited (HTTP 429)",
                error_code="rate_limited",
                retry_after=retry_after,
            )

        if response.status_code == 404:
            return LookupResult(kind="no_chapters", error="No chapters found for ASIN")

        if response.status_code >= 500:
            return LookupResult(
                kind="temporary_error",
                error=f"Audnexus server error (HTTP {response.status_code})",
                error_code=f"http_{response.status_code}",
            )

        if response.status_code != 200:
            return LookupResult(
                kind="unexpected",
                error=f"Unexpected Audnexus response (HTTP {response.status_code})",
                error_code=f"http_{response.status_code}",
            )

        try:
            data: dict[str, Any] = response.json()
        except ValueError:
            return LookupResult(
                kind="unexpected",
                error="Audnexus returned invalid JSON",
                error_code="invalid_json",
            )

        chapters_data = data.get("chapters")
        if not chapters_data:
            return LookupResult(kind="no_chapters", error="No chapter data in response")

        chapters: list[Chapter] = []
        for item in chapters_data:
            try:
                start_ms = item.get("startOffsetMs")
                length_ms = item.get("lengthMs")
                if start_ms is None or length_ms is None:
                    continue
                start = float(start_ms) / 1000.0
                end = start + (float(length_ms) / 1000.0)
                chapters.append(
                    Chapter(
                        title=(item.get("title") or "").strip() or f"Chapter {len(chapters) + 1}",
                        start_seconds=start,
                        end_seconds=end,
                    )
                )
            except (TypeError, ValueError):
                continue

        if not chapters:
            return LookupResult(kind="no_chapters", error="No valid chapters parsed")

        return LookupResult(
            kind="success",
            chapters=chapters,
            recording=recording,
            recording_id=asin,
        )