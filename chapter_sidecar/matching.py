"""Recording match selection and ranking.

Finds the correct audiobook recording, not merely a book with a similar
title. Exact identifier matches (ASIN/ISBN) take precedence over
title+author similarity. Ambiguous candidates are rejected rather than
silently choosing the first result.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass(frozen=True)
class RecordingCandidate:
    """A candidate recording from a provider search."""

    provider: str
    recording_id: str
    title: str
    author: str | None
    duration_seconds: float | None
    raw: dict


def normalise_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.casefold().strip().split())


def similarity(left: str | None, right: str | None) -> float:
    return SequenceMatcher(
        None,
        normalise_text(left),
        normalise_text(right),
    ).ratio()


def rank_recordings(
    candidates: list[RecordingCandidate],
    *,
    title: str,
    author: str | None = None,
    asin: str | None = None,
    isbn: str | None = None,
) -> list[tuple[float, RecordingCandidate]]:
    """Rank candidates by relevance.

    Exact identifier match (ASIN or ISBN) scores 1.0 and takes precedence.
    Otherwise combines title and author similarity. A missing author is not
    treated as a perfect author match.
    """
    ranked: list[tuple[float, RecordingCandidate]] = []

    for candidate in candidates:
        raw = candidate.raw

        candidate_asin = raw.get("asin")
        candidate_isbn = raw.get("isbn")

        if asin and candidate_asin and candidate_asin == asin:
            ranked.append((1.0, candidate))
            continue

        if isbn and candidate_isbn and candidate_isbn == isbn:
            ranked.append((1.0, candidate))
            continue

        title_score = similarity(title, candidate.title)

        author_score: float | None = None
        if author and candidate.author:
            author_score = similarity(author, candidate.author)

        if author_score is not None:
            score = 0.75 * title_score + 0.25 * author_score
        else:
            score = title_score * 0.75

        ranked.append((score, candidate))

    return sorted(ranked, key=lambda item: item[0], reverse=True)


def select_best(
    ranked: list[tuple[float, RecordingCandidate]],
    *,
    title: str,
    author: str | None = None,
    title_threshold: float = 0.85,
    author_threshold: float | None = None,
) -> RecordingCandidate | None:
    """Select the single best recording from ranked candidates.

    Returns None if no candidate meets the acceptance thresholds or if the
    match is ambiguous (top two candidates within 0.05 of each other).
    """
    if not ranked:
        return None

    top_score, top_candidate = ranked[0]

    # Title-based match: require minimum similarity
    if top_score < title_threshold:
        return None

    # If author is provided and candidate has author, check author threshold
    if author and author_threshold is not None and top_candidate.author:
        if similarity(author, top_candidate.author) < author_threshold:
            return None

    # Ambiguity check: if a close second exists with a different recording id,
    # reject rather than guess. This check runs before accepting exact matches
    # because two different recordings can share identical title+author.
    if len(ranked) >= 2:
        second_score, second_candidate = ranked[1]
        if top_score - second_score < 0.05:
            return None

    # Exact identifier match: accept unconditionally (only reached if not ambiguous)
    if top_score >= 1.0:
        return top_candidate

    return top_candidate