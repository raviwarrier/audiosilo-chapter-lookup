"""AudioSilo Meta provider adapter (Audbookshelf-compatible search).

Candidate endpoints (must be verified against the live service):
    GET https://meta.audiosilo.app/abs/search   (title/author/ISBN search)
    GET https://api.audnex.us/books/{ASIN}/chapters?region={region}  (via Audnexus)

This provider resolves recordings via search and delegates chapter
retrieval to Audnexus. It is a thin adapter; the actual chapter fetching
is done by AudnexusProvider.
"""

from __future__ import annotations

from typing import Any

import requests

from .base import Chapter, LookupResult, RecordingMatch


class AudioSiloMetaProvider:
    name = "audiosilo_meta"

    def __init__(
        self,
        *,
        base_url: str = "https://meta.audiosilo.app",
        timeout: int = 20,
        audnexus_region: str = "US",
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.audnexus_region = audnexus_region

    def search(
        self,
        *,
        title: str,
        author: str | None,
        isbn: str | None,
        asin: str | None,
    ) -> list[RecordingMatch]:
        """Search AudioSilo Meta's ABS-compatible search endpoint.

        Candidate inputs: title, author, ISBN. Returns recording candidates
        with any identifiers found (ASIN, ISBN, etc.).
        """
        params: dict[str, str] = {}
        if title:
            params["title"] = title
        if author:
            params["author"] = author
        if isbn:
            params["isbn"] = isbn
        if asin:
            params["asin"] = asin

        if not params:
            return []

        try:
            response = requests.get(
                f"{self.base_url}/abs/search",
                params=params,
                timeout=self.timeout,
            )
        except requests.exceptions.Timeout:
            return []
        except requests.exceptions.RequestException:
            return []

        if response.status_code != 200:
            return []

        try:
            data: Any = response.json()
        except ValueError:
            return []

        results = data if isinstance(data, list) else data.get("results", [])
        matches: list[RecordingMatch] = []

        for item in results if isinstance(results, list) else []:
            if not isinstance(item, dict):
                continue
            matches.append(
                RecordingMatch(
                    provider=self.name,
                    recording_id=str(item.get("asin") or item.get("id") or ""),
                    title=str(item.get("title") or ""),
                    author=str(item.get("author") or item.get("artist") or ""),
                    duration_seconds=None,
                    raw=item,
                )
            )

        return matches

    def get_chapters(
        self,
        recording: RecordingMatch,
    ) -> LookupResult:
        """This provider does not fetch chapters directly. Use AudnexusProvider
        for chapter retrieval after a recording is selected."""
        return LookupResult(
            kind="unexpected",
            error="AudioSilo Meta provider does not support get_chapters; use Audnexus",
        )