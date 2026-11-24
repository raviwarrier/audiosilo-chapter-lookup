"""Dry-run output writer.

In dry-run mode, instead of updating AudioSilo, writes a [title]-chapters.txt
file containing the proposed chapter payload for manual review. Once the user
verifies a file, they can set DRY_RUN=false to enable real AudioSilo writes.
"""

from __future__ import annotations

from pathlib import Path

from .chapters import Chapter


class DryRunWriter:
    def __init__(self, output_dir: str | Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def sanitize_filename(self, title: str) -> str:
        """Make a title safe for use as a filename."""
        safe = "".join(c if c.isalnum() or c in (" ", "-", "_") else "_" for c in title)
        safe = "_".join(safe.split())
        return safe or "unknown"

    def write(
        self,
        title: str,
        author: str | None,
        book_path: str,
        chapters: list[Chapter],
        recording_id: str | None = None,
        provider: str | None = None,
    ) -> Path:
        """Write a [title]-chapters.txt dry-run file and return its path."""
        filename = f"{self.sanitize_filename(title)}-chapters.txt"
        filepath = self.output_dir / filename

        lines: list[str] = []
        lines.append("=== AudioSilo Chapter Sidecar — Dry Run ===")
        lines.append("")
        lines.append(f"Title: {title}")
        if author:
            lines.append(f"Author: {author}")
        lines.append(f"Book path: {book_path}")
        lines.append(f"Provider: {provider or 'unknown'}")
        lines.append(f"Recording ID: {recording_id or 'unknown'}")
        lines.append(f"Chapter count: {len(chapters)}")
        lines.append("")
        lines.append("Proposed chapters (seconds):")
        lines.append("-" * 50)
        for index, chapter in enumerate(chapters, start=1):
            lines.append(
                f"  {index:3d}. {chapter.title!r}  "
                f"start={chapter.start_seconds:.3f}s  "
                f"end={chapter.end_seconds:.3f}s  "
                f"duration={chapter.end_seconds - chapter.start_seconds:.3f}s"
            )
        lines.append("")
        lines.append("=== End of dry-run proposal ===")
        lines.append("")
        lines.append(
            "To apply these chapters to AudioSilo, verify this file, then set "
            "DRY_RUN=false in .env and re-run the processor."
        )

        filepath.write_text("\n".join(lines), encoding="utf-8")
        return filepath