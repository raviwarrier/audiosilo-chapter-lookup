"""Reporting for the chapter sidecar.

Summarises processing counts by status and provider from the sidecar
state database. Does not modify AudioSilo.
"""

from __future__ import annotations

from .database import SidecarDatabase


def status_summary(store: SidecarDatabase, library_id: str) -> dict[str, int]:
    """Return counts of book jobs grouped by status."""
    return store.count_by_status(library_id)


def provider_summary(store: SidecarDatabase, library_id: str) -> dict[str, int]:
    """Return counts of books grouped by external provider."""
    jobs = store.get_all(library_id)
    counts: dict[str, int] = {}
    for job in jobs:
        provider = job.external_provider or "none"
        counts[provider] = counts.get(provider, 0) + 1
    return counts


def format_report(store: SidecarDatabase, library_id: str) -> str:
    """Build a human-readable report string."""
    lines: list[str] = []
    lines.append("=== AudioSilo Chapter Sidecar Report ===")
    lines.append(f"Library ID: {library_id}")
    lines.append("")

    status_counts = status_summary(store, library_id)
    lines.append("By status:")
    if status_counts:
        for status, count in sorted(status_counts.items()):
            lines.append(f"  {status:30s} {count}")
    else:
        lines.append("  (no books processed)")
    lines.append("")

    provider_counts = provider_summary(store, library_id)
    lines.append("By provider:")
    if provider_counts:
        for provider, count in sorted(provider_counts.items()):
            lines.append(f"  {provider:30s} {count}")
    else:
        lines.append("  (none)")
    lines.append("")

    total = sum(status_counts.values())
    lines.append(f"Total books tracked: {total}")
    lines.append("=== End of report ===")
    return "\n".join(lines)