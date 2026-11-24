"""Processing orchestration for the chapter sidecar.

High-level workflow per book (from the spec, Section 4):

1. Fetch the current AudioSilo book record.
2. Determine whether it already has usable chapters.
3. If yes, record already_has_chapters and stop (unless force).
4. Check AudioSilo community chapters.
5. Re-fetch and check for usable community chapters.
6. If community failed, search external providers.
7. Match the correct recording (not just similar title).
8. Retrieve the chapter list.
9. Convert and validate against the actual AudioSilo duration.
10. Dry-run: write [title]-chapters.txt and stop.
11. Real mode: write chapters via AudioSilo API.
12. Re-fetch and verify chapters appeared.
13. Mark complete only after verification.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .audiosilo_client import AudioSiloClient
from .chapters import Chapter, validate_chapters
from .community import CommunityChecker
from .config import Config
from .database import BookJob, SidecarDatabase
from .dryrun_writer import DryRunWriter
from .matching import rank_recordings, select_best
from .providers.base import LookupResult


@dataclass
class ProcessResult:
    status: str
    book: BookJob
    chapters: Optional[list[Chapter]] = None
    message: str = ""
    dry_run: bool = False


class Processor:
    def __init__(
        self,
        client: AudioSiloClient,
        community: CommunityChecker,
        providers: list,
        store: SidecarDatabase,
        dry_run_writer: DryRunWriter,
        config: Config,
    ):
        self.client = client
        self.community = community
        self.providers = providers
        self.store = store
        self.dry_run_writer = dry_run_writer
        self.config = config

    def process_one(
        self,
        book_path: str,
        title: Optional[str] = None,
        author: Optional[str] = None,
        dry_run: Optional[bool] = None,
        force: bool = False,
    ) -> ProcessResult:
        if dry_run is None:
            dry_run = self.config.dry_run

        library_id = self.config.audiosilo_library_id or ""
        job = self.store.ensure_pending(library_id, book_path)

        # 1. Fetch current book record
        try:
            current = self.client.get_book(book_path)
        except Exception as exc:
            job.status = "lookup_failed"
            job.error_code = "fetch_failed"
            job.error_message = str(exc)
            job.last_attempt_at = job.updated_at
            self.store.upsert(job)
            return ProcessResult(
                status="lookup_failed",
                book=job,
                message=f"Failed to fetch book: {exc}",
            )

        if title:
            job.title = title
        if author:
            job.author = author

        # Extract identifiers from the AudioSilo book record
        book_asin = current.get("asin") or None
        book_isbn = current.get("isbn") or None

        # 2-3. Already has usable chapters?
        if self.client.has_usable_chapters(current) and not force:
            job.status = "already_has_chapters"
            job.completed_at = job.updated_at
            self.store.upsert(job)
            return ProcessResult(
                status="already_has_chapters",
                book=job,
                message="Book already has usable chapters",
            )

        # 4-5. Community chapters
        try:
            community_result = self.community.obtain_chapters(
                book_path, current_book=current
            )
        except TimeoutError as exc:
            job.status = "lookup_failed"
            job.error_code = "community_timeout"
            job.error_message = str(exc)
            job.last_attempt_at = job.updated_at
            self.store.upsert(job)
            return ProcessResult(
                status="lookup_failed",
                book=job,
                message=str(exc),
            )
        except Exception as exc:
            job.status = "lookup_failed"
            job.error_code = "community_error"
            job.error_message = str(exc)
            job.last_attempt_at = job.updated_at
            self.store.upsert(job)
            return ProcessResult(
                status="lookup_failed",
                book=job,
                message=f"Community check failed: {exc}",
            )

        if community_result:
            try:
                verified = self.client.get_book(book_path)
            except Exception as exc:
                job.status = "lookup_failed"
                job.error_code = "refetch_failed"
                job.error_message = str(exc)
                job.last_attempt_at = job.updated_at
                self.store.upsert(job)
                return ProcessResult(
                    status="lookup_failed",
                    book=job,
                    message=f"Failed to re-fetch after community check: {exc}",
                )

            if self.client.has_usable_chapters(verified):
                job.status = "community_success"
                job.completed_at = job.updated_at
                self.store.upsert(job)
                return ProcessResult(
                    status="community_success",
                    book=job,
                    message="Community chapters are now available",
                )

        # 6-8. External fallback
        duration = self._extract_duration(current)

        for provider in self.providers:
            try:
                candidates = provider.search(
                    title=title or "",
                    author=author,
                    isbn=book_isbn,
                    asin=book_asin,
                )
            except Exception as exc:
                job.status = "lookup_failed"
                job.error_code = "provider_search_error"
                job.error_message = f"{provider.name}: {exc}"
                job.last_attempt_at = job.updated_at
                self.store.upsert(job)
                return ProcessResult(
                    status="lookup_failed",
                    book=job,
                    message=f"Provider {provider.name} search failed: {exc}",
                )

            ranked = rank_recordings(
                candidates,
                title=title or "",
                author=author,
                asin=book_asin,
                isbn=book_isbn,
            )
            best = select_best(
                ranked,
                title=title or "",
                author=author,
            )

            if not best:
                continue

            try:
                result = provider.get_chapters(best)
            except Exception as exc:
                job.status = "lookup_failed"
                job.error_code = "provider_chapter_error"
                job.error_message = f"{provider.name}: {exc}"
                job.last_attempt_at = job.updated_at
                self.store.upsert(job)
                return ProcessResult(
                    status="lookup_failed",
                    book=job,
                    message=f"Provider {provider.name} chapter fetch failed: {exc}",
                )

            if result.kind == "temporary_error":
                job.status = "lookup_failed"
                job.error_code = "provider_temporary_error"
                job.error_message = result.error or str(exc)
                job.retry_after = (
                    str(result.retry_after) if result.retry_after else None
                )
                job.last_attempt_at = job.updated_at
                self.store.upsert(job)
                return ProcessResult(
                    status="lookup_failed",
                    book=job,
                    message=f"Provider {provider.name} temporary error: {result.error}",
                )

            if result.kind == "ambiguous":
                job.status = "ambiguous_match"
                job.completed_at = job.updated_at
                self.store.upsert(job)
                return ProcessResult(
                    status="ambiguous_match",
                    book=job,
                    message="Ambiguous recording match; needs review",
                )

            if result.kind != "success" or not result.chapters:
                continue

            chapters = result.chapters

            if duration is None:
                job.status = "lookup_failed"
                job.error_code = "no_duration"
                job.error_message = "Could not determine audiobook duration"
                job.last_attempt_at = job.updated_at
                self.store.upsert(job)
                return ProcessResult(
                    status="lookup_failed",
                    book=job,
                    message="Could not determine audiobook duration for validation",
                )

            try:
                validated = validate_chapters(
                    chapters,
                    audiobook_duration=duration,
                    tolerance=self.config.duration_tolerance_seconds,
                )
            except ValueError:
                continue

            if dry_run:
                output_path = self.dry_run_writer.write(
                    title=title or job.title or "unknown",
                    author=author,
                    book_path=book_path,
                    chapters=validated,
                    recording_id=result.recording_id,
                    provider=provider.name,
                )
                job.status = "pending"
                job.external_provider = provider.name
                job.external_recording_id = result.recording_id
                job.chapter_count = len(validated)
                job.source_payload_hash = self.client.payload_hash(validated)
                job.last_attempt_at = job.updated_at
                self.store.upsert(job)
                return ProcessResult(
                    status="dry_run_success",
                    book=job,
                    chapters=validated,
                    message=f"Dry-run chapter proposal written to {output_path}",
                    dry_run=True,
                )

            try:
                write_result = self.client.write_chapters(book_path, validated)
            except NotImplementedError as exc:
                job.status = "write_failed"
                job.error_code = "write_not_implemented"
                job.error_message = str(exc)
                job.last_attempt_at = job.updated_at
                self.store.upsert(job)
                return ProcessResult(
                    status="write_failed",
                    book=job,
                    message=str(exc),
                )
            except Exception as exc:
                job.status = "write_failed"
                job.error_code = "write_error"
                job.error_message = str(exc)
                job.last_attempt_at = job.updated_at
                self.store.upsert(job)
                return ProcessResult(
                    status="write_failed",
                    book=job,
                    message=f"Write failed: {exc}",
                )

            # Direct write returns a result dict instead of raising
            if isinstance(write_result, dict):
                if write_result.get("error"):
                    job.status = "write_failed"
                    job.error_code = "direct_write_error"
                    job.error_message = write_result.get("error", "unknown error")
                    job.last_attempt_at = job.updated_at
                    self.store.upsert(job)
                    return ProcessResult(
                        status="write_failed",
                        book=job,
                        message=write_result.get("message", "Direct write failed"),
                        chapters=validated,
                    )
                # Direct write succeeded (dry run or real)
                if write_result.get("dry_run"):
                    job.status = "pending"
                    job.last_attempt_at = job.updated_at
                    self.store.upsert(job)
                    return ProcessResult(
                        status="dry_run_success",
                        book=job,
                        chapters=validated,
                        message=write_result.get(
                            "message", "Dry run: direct write not performed"
                        ),
                        dry_run=True,
                    )

                # Real direct write succeeded - store backup info
                job.status = "external_success"
                job.external_provider = "direct_write"
                job.external_recording_id = write_result.get(
                    "method", "direct"
                )
                job.chapter_count = len(validated)
                job.completed_at = job.updated_at
                job.last_attempt_at = job.updated_at
                # Store backup path in error_message for reference (not an error)
                job.error_message = f"backup: {write_result.get('backup_path', 'unknown')}"
                self.store.upsert(job)
                return ProcessResult(
                    status="external_success",
                    book=job,
                    chapters=validated,
                    message=write_result.get(
                        "message", f"Wrote {len(validated)} chapters via direct write"
                    ),
                )

            try:
                saved = self.client.get_book(book_path)
            except Exception as exc:
                job.status = "write_failed"
                job.error_code = "verify_fetch_failed"
                job.error_message = str(exc)
                job.last_attempt_at = job.updated_at
                self.store.upsert(job)
                return ProcessResult(
                    status="write_failed",
                    book=job,
                    message=f"Write succeeded but verification fetch failed: {exc}",
                )

            if not self.client.has_usable_chapters(saved):
                job.status = "write_failed"
                job.error_code = "write_not_verified"
                job.error_message = "Chapters not exposed by AudioSilo after write"
                job.last_attempt_at = job.updated_at
                self.store.upsert(job)
                return ProcessResult(
                    status="write_failed",
                    book=job,
                    message="Write succeeded but chapters not verified in AudioSilo",
                )

            job.status = "external_success"
            job.external_provider = provider.name
            job.external_recording_id = result.recording_id
            job.chapter_count = len(validated)
            job.completed_at = job.updated_at
            job.last_attempt_at = job.updated_at
            self.store.upsert(job)
            return ProcessResult(
                status="external_success",
                book=job,
                chapters=validated,
                message=f"Wrote {len(validated)} chapters and verified in AudioSilo",
            )

        job.status = "unavailable"
        job.completed_at = job.updated_at
        self.store.upsert(job)
        return ProcessResult(
            status="unavailable",
            book=job,
            message="No provider supplied usable chapters",
        )

    def process_batch(
        self,
        batch_number: int,
        limit: Optional[int] = None,
        dry_run: Optional[bool] = None,
    ) -> list[ProcessResult]:
        """Process a stable, reproducible batch of pending books.

        Batches are numbered by the operator. Ordering is deterministic:
        books sorted by book_path. batch_number selects which slice
        (each batch is `limit` books, starting at (batch_number - 1) * limit).
        """
        if dry_run is None:
            dry_run = self.config.dry_run

        import time as _time

        library_id = self.config.audiosilo_library_id or ""
        pending = self.store.get_by_status(library_id, "pending")
        pending.sort(key=lambda job: job.book_path)

        if limit is None or limit <= 0:
            limit = 10

        start = (batch_number - 1) * limit
        end = start + limit
        batch = pending[start:end]

        results: list[ProcessResult] = []
        for job in batch:
            result = self.process_one(
                book_path=job.book_path,
                title=job.title,
                author=job.author,
                dry_run=dry_run,
            )
            results.append(result)

            if self.config.request_delay_seconds > 0:
                _time.sleep(self.config.request_delay_seconds)

        return results

    def process_all(
        self,
        dry_run: Optional[bool] = None,
    ) -> list[ProcessResult]:
        """Process all pending books (conservative, one at a time)."""
        if dry_run is None:
            dry_run = self.config.dry_run

        import time as _time

        library_id = self.config.audiosilo_library_id or ""
        pending = self.store.get_by_status(library_id, "pending")
        pending.sort(key=lambda job: job.book_path)

        results: list[ProcessResult] = []
        for job in pending:
            result = self.process_one(
                book_path=job.book_path,
                title=job.title,
                author=job.author,
                dry_run=dry_run,
            )
            results.append(result)

            if self.config.request_delay_seconds > 0:
                _time.sleep(self.config.request_delay_seconds)

        return results

    def retry(
        self,
        dry_run: Optional[bool] = None,
    ) -> list[ProcessResult]:
        """Retry lookup_failed and write_failed books."""
        if dry_run is None:
            dry_run = self.config.dry_run

        import time as _time

        library_id = self.config.audiosilo_library_id or ""
        retryable: list[BookJob] = []
        for status in ("lookup_failed", "write_failed"):
            retryable.extend(self.store.get_by_status(library_id, status))

        retryable.sort(key=lambda job: job.book_path)

        results: list[ProcessResult] = []
        for job in retryable:
            job.error_code = None
            job.error_message = None
            job.retry_after = None
            self.store.upsert(job)

            result = self.process_one(
                book_path=job.book_path,
                title=job.title,
                author=job.author,
                dry_run=dry_run,
            )
            results.append(result)

            if self.config.request_delay_seconds > 0:
                _time.sleep(self.config.request_delay_seconds)

        return results

    def _extract_duration(self, book: dict) -> Optional[float]:
        """Extract audiobook duration from a book response via the client."""
        return self.client.extract_duration(book)