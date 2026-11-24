"""AudioSilo community chapter check polling.

Starts a community check once, polls the book endpoint at a sensible
interval, applies a timeout, and stops polling when the check is no longer
running. Never starts the same check twice while it is already running.
"""

from __future__ import annotations

import time

from .audiosilo_client import AudioSiloClient


class CommunityChecker:
    def __init__(self, client: AudioSiloClient, config):
        self.client = client
        self.config = config

    def obtain_chapters(
        self,
        book_path: str,
        current_book: dict | None = None,
    ) -> dict | None:
        """Check community chapter status.

        Returns a dict with keys:
          - chapters: list[Chapter] | None
          - recording_id: str | None
          - source: "community"
        or None if no usable community chapters are available.

        Behaviour:
        - If the book already has usable chapters, return None (skip).
        - If a community check is already running, poll until it finishes.
        - If no check has been attempted, start one and poll.
        - Re-fetch the book after the check and determine if usable chapters exist.
        """
        book = current_book or self.client.get_book(book_path)

        state = self.client.community_check_state(book)

        # If a check is already running, poll it
        if state.get("checking"):
            book = self._poll_until_done(book_path)
            state = self.client.community_check_state(book)

        # If no check has been attempted, start one
        elif not state.get("checked"):
            self.client.start_community_check(book_path)
            book = self._poll_until_done(book_path)
            state = self.client.community_check_state(book)

        if state.get("has_community_chapters"):
            return {
                "chapters": None,  # Community chapters are exposed by AudioSilo itself
                "recording_id": None,
                "source": "community",
            }

        return None

    def _poll_until_done(self, book_path: str, book: dict | None = None) -> dict:
        deadline = time.monotonic() + self.config.community_poll_timeout
        interval = self.config.community_poll_interval

        current = book or self.client.get_book(book_path)

        while time.monotonic() < deadline:
            state = self.client.community_check_state(current)
            checking = state.get("checking")

            if checking is False:
                return current

            if checking is None:
                raise RuntimeError(
                    "Community-check state field is missing; "
                    "verify the installed API response schema"
                )

            time.sleep(interval)
            current = self.client.get_book(book_path)

        raise TimeoutError(
            f"Community chapter check did not finish within "
            f"{self.config.community_poll_timeout}s"
        )