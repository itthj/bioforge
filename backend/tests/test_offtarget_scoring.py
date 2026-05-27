"""Tests for offtarget_scoring (MIT / Hsu 2013 scoring).

The math is short but the boundary cases matter — seed vs distal mismatches,
short guides, gaps, missing-alignment fallback. These tests pin the published
behaviour.
"""

from __future__ import annotations

import pytest
from bioforge.tools.sequence.offtarget_scoring import (
    HSU_2013_WEIGHTS,
    mit_score_from_positions,
    positions_from_alignment,
    score_offtarget,
)

# --- Position extraction -------------------------------------------------------


def test_positions_from_perfect_match() -> None:
    """Identical strings → no mismatch positions."""
    positions = positions_from_alignment(
        guide_seq="ATGCATGCATGCATGCATGC",
        query_aligned="ATGCATGCATGCATGCATGC",
        subject_aligned="ATGCATGCATGCATGCATGC",
    )
    assert positions == []


def test_positions_identify_single_substitution() -> None:
    # Mismatch at position 5 of a 20-nt guide.
    positions = positions_from_alignment(
        guide_seq="ATGCATGCATGCATGCATGC",
        query_aligned="ATGCATGCATGCATGCATGC",
        subject_aligned="ATGCTTGCATGCATGCATGC",  # ^ pos 5: A→T
    )
    assert positions == [5]


def test_positions_identify_multiple_substitutions() -> None:
    positions = positions_from_alignment(
        guide_seq="ATGCATGCATGCATGCATGC",
        query_aligned="ATGCATGCATGCATGCATGC",
        subject_aligned="TTGCATGCATGCATGCATTT",  # pos 1: A→T; pos 19-20: GC→TT
    )
    assert positions == [1, 19, 20]


def test_positions_handle_subject_gap_as_mismatch() -> None:
    """A gap in the subject means the off-target lost a base — treated as mismatch."""
    positions = positions_from_alignment(
        guide_seq="ATGCATGCAT",
        query_aligned="ATGCATGCAT",
        subject_aligned="AT-CATGCAT",  # pos 3 gapped
    )
    assert positions == [3]


def test_positions_skip_query_gap() -> None:
    """A gap in the QUERY means the alignment inserted a base into the guide —
    that's an insertion, not a substitution, so we don't surface a position."""
    positions = positions_from_alignment(
        guide_seq="ATGCATGCAT",
        query_aligned="ATG-CATGCAT",
        subject_aligned="ATGGCATGCAT",
    )
    # No substitution mismatches in the guide.
    assert positions == []


def test_positions_returns_empty_on_missing_alignment() -> None:
    """Empty alignment strings → empty positions (caller falls back)."""
    assert positions_from_alignment(guide_seq="ATGC", query_aligned="", subject_aligned="") == []


# --- MIT score math -------------------------------------------------------------


def test_mit_score_perfect_match_is_one() -> None:
    assert mit_score_from_positions([], guide_length=20) == 1.0


def test_mit_score_distal_mismatches_barely_hurt() -> None:
    """Positions 1-2 have weight 0.0 — score should stay at 1.0."""
    score = mit_score_from_positions([1, 2], guide_length=20)
    assert score == pytest.approx(1.0)


def test_mit_score_seed_mismatch_hurts_a_lot() -> None:
    """Mismatch at position 14 (weight 0.851) drops score to 1 - 0.851 = 0.149."""
    score = mit_score_from_positions([14], guide_length=20)
    assert score == pytest.approx(1.0 - HSU_2013_WEIGHTS[13], abs=1e-6)
    assert score < 0.2


def test_mit_score_multiple_seed_mismatches_compound() -> None:
    """Two seed mismatches multiply penalties."""
    score = mit_score_from_positions([14, 16], guide_length=20)
    expected = (1.0 - HSU_2013_WEIGHTS[13]) * (1.0 - HSU_2013_WEIGHTS[15])
    assert score == pytest.approx(expected, abs=1e-6)
    assert score < 0.05


def test_mit_score_pam_proximal_position_uses_last_weight() -> None:
    """Position 20 (PAM-adjacent) uses weight index 19 (0.583)."""
    score = mit_score_from_positions([20], guide_length=20)
    assert score == pytest.approx(1.0 - HSU_2013_WEIGHTS[19])


def test_mit_score_handles_short_guide_by_aligning_to_pam_end() -> None:
    """An 18-nt guide aligns to weight indices 2-19 (the PAM-proximal end is
    what's biologically conserved)."""
    # Position 18 of an 18-nt guide is PAM-adjacent → weight index 19.
    score = mit_score_from_positions([18], guide_length=18)
    assert score == pytest.approx(1.0 - HSU_2013_WEIGHTS[19])


# --- End-to-end score_offtarget ------------------------------------------------


def test_score_offtarget_full_alignment_path() -> None:
    result = score_offtarget(
        guide_seq="ATGCATGCATGCATGCATGC",
        query_aligned="ATGCATGCATGCATGCATGC",
        subject_aligned="ATGCATGCATGCATGCATGT",  # pos 20 mismatch
        mismatch_count_fallback=1,
    )
    assert result.used_full_alignment is True
    assert result.mismatch_positions == [20]
    assert result.score == pytest.approx(1.0 - HSU_2013_WEIGHTS[19])


def test_score_offtarget_fallback_when_alignment_missing() -> None:
    """No alignment strings → fall back to count-only. Mismatches are
    placed at PAM-distal positions (weight 0), so the fallback score is
    optimistically high — flagged for the caller's caveat list."""
    result = score_offtarget(
        guide_seq="ATGCATGCATGCATGCATGC",
        query_aligned="",
        subject_aligned="",
        mismatch_count_fallback=2,
    )
    assert result.used_full_alignment is False
    # Positions 1 and 2 have weight 0.0 → score stays at 1.0.
    assert result.score == pytest.approx(1.0)


def test_score_offtarget_fallback_with_no_mismatches() -> None:
    result = score_offtarget(
        guide_seq="ATGCATGCATGCATGCATGC",
        query_aligned="",
        subject_aligned="",
        mismatch_count_fallback=0,
    )
    assert result.used_full_alignment is False
    assert result.score == 1.0
    assert result.mismatch_positions == []
