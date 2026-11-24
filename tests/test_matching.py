"""Tests for recording match selection and ranking."""

from chapter_sidecar.matching import RecordingCandidate, rank_recordings, select_best


def make_candidate(recording_id, title, author=None, raw=None):
    return RecordingCandidate(
        provider="test",
        recording_id=recording_id,
        title=title,
        author=author,
        duration_seconds=None,
        raw=raw or {},
    )


def test_exact_asin_match_takes_precedence():
    candidates = [
        make_candidate("B000000002", "Different Title", raw={"asin": "B000000002"}),
        make_candidate("B000000001", "My Book Title", raw={"asin": "B000000001"}),
    ]
    ranked = rank_recordings(candidates, title="My Book Title", asin="B000000001")
    assert ranked[0][0] == 1.0
    assert ranked[0][1].recording_id == "B000000001"


def test_title_author_similarity():
    candidates = [
        make_candidate("id1", "My Book Title", author="John Author"),
        make_candidate("id2", "Completely Different", author="Someone Else"),
    ]
    ranked = rank_recordings(candidates, title="My Book Title", author="John Author")
    assert ranked[0][1].recording_id == "id1"
    assert ranked[0][0] > ranked[1][0]


def test_select_best_accepts_good_match():
    candidates = [
        make_candidate("id1", "My Book Title", author="John Author"),
    ]
    ranked = rank_recordings(candidates, title="My Book Title", author="John Author")
    best = select_best(ranked, title="My Book Title", author="John Author")
    assert best is not None
    assert best.recording_id == "id1"


def test_select_best_rejects_poor_match():
    candidates = [
        make_candidate("id1", "Completely Unrelated Title", author="Someone"),
    ]
    ranked = rank_recordings(candidates, title="My Book Title", author="John Author")
    best = select_best(ranked, title="My Book Title", author="John Author")
    assert best is None


def test_select_best_rejects_ambiguous():
    candidates = [
        make_candidate("id1", "My Book Title", author="John Author"),
        make_candidate("id2", "My Book Title", author="John Author"),
    ]
    ranked = rank_recordings(candidates, title="My Book Title", author="John Author")
    best = select_best(ranked, title="My Book Title", author="John Author")
    assert best is None