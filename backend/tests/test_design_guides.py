"""Tests for design_guides.

Strategy: construct target sequences with KNOWN PAM-site counts at known positions so
coordinate handling, strand mapping, and metric calculation can all be verified
deterministically. Each test owns its expected counts via the construction.
"""

from __future__ import annotations

import pydantic
import pytest
from Bio.Seq import Seq
from bioforge.tools.sequence.design_guides import (
    DesignGuidesInput,
    _longest_run,
    _longest_selfcomp,
    _score,
    design_guides,
)

# --- Metric-helper unit tests --------------------------------------------------------


def test_longest_run_any_base() -> None:
    assert _longest_run("ACGT") == 1
    assert _longest_run("AAAA") == 4
    assert _longest_run("ACGAAAG") == 3
    assert _longest_run("") == 0


def test_longest_run_specific_base() -> None:
    assert _longest_run("ATTTTGCAT", "T") == 4
    assert _longest_run("ACGACGACG", "T") == 0  # no T at all


def test_longest_selfcomp_finds_palindromic_run() -> None:
    # GAATTC ↔ GAATTC (EcoRI palindrome, fully self-complementary)
    assert _longest_selfcomp("GAATTC") == 6
    # ACACACAC's rev-comp is GTGTGTGT — no shared k-mers, so selfcomp = 0
    assert _longest_selfcomp("ACACACAC") == 0


def test_score_optimal_guide() -> None:
    """50% GC, no polyT, no mononuc run, no self-comp → score 1.0."""
    s = _score(gc_pct=50.0, longest_t=2, longest_run=2, selfcomp=3)
    assert s.heuristic_score == 1.0


def test_score_polyt_zeroes_that_component() -> None:
    s = _score(gc_pct=50.0, longest_t=5, longest_run=5, selfcomp=3)
    # polyt and mononuc both zeroed (0.3 + 0.2); gc + selfcomp survive (0.4 + 0.1)
    assert s.polyt_score == 0.0
    assert s.mononuc_score == 0.0
    assert s.heuristic_score == pytest.approx(0.5)


# --- Whole-tool integration tests ----------------------------------------------------


# Build a 50-nt target with exactly ONE NGG PAM on the forward strand at position 30.
# Choose a balanced 20-nt protospacer for good metrics.
_BALANCED_20NT = "ACGTACGTACGTACGTACGT"  # 50% GC, 25% each, no runs
_PROBLEM_FREE_TARGET = (
    "AAAAAAAAAA"  # 10-nt 5' filler so PAM has room for guide upstream
    + _BALANCED_20NT  # 20-nt protospacer at positions 10-30
    + "AGG"  # NGG PAM at positions 30-33
    + "TTTTTTTTTTTTTTTTT"  # 17-nt 3' filler (total 50 nt)
)


async def test_finds_single_forward_strand_pam() -> None:
    out = await design_guides(DesignGuidesInput(sequence=_PROBLEM_FREE_TARGET, strands=["+"]))
    assert out.num_returned >= 1
    g = next((x for x in out.guides if x.protospacer == _BALANCED_20NT), None)
    assert g is not None, f"Did not find the constructed protospacer in {out.guides}"
    assert g.strand == "+"
    assert g.protospacer_start == 10
    assert g.protospacer_end == 30
    assert g.pam_start == 30
    assert g.pam_end == 33
    assert g.pam_sequence == "AGG"
    assert g.gc_percent == 50.0


async def test_no_pam_returns_empty_with_note() -> None:
    # 30 A's: no G's anywhere → no NGG PAM possible
    out = await design_guides(DesignGuidesInput(sequence="A" * 30))
    assert out.num_returned == 0
    assert out.guides == []
    assert any("No NGG PAM" in n for n in out.notes)


async def test_reverse_strand_pam_coordinates_map_to_forward() -> None:
    """A PAM on the reverse strand must be reported with strand='-' and forward-strand
    coordinates that point into the right region of the input."""
    # Put the PAM site on the REVERSE strand by reverse-complementing the construct.
    fwd = str(Seq(_PROBLEM_FREE_TARGET).reverse_complement())
    out = await design_guides(DesignGuidesInput(sequence=fwd, strands=["-"]))
    rev_guides = [g for g in out.guides if g.strand == "-"]
    assert rev_guides, f"Expected at least one - strand guide, got {out.guides}"
    g = rev_guides[0]
    # Forward-strand coordinates should be within the input length.
    assert 0 <= g.protospacer_start < g.protospacer_end <= len(fwd)
    assert 0 <= g.pam_start < g.pam_end <= len(fwd)
    # Protospacer length consistent with guide_length=20.
    assert g.protospacer_end - g.protospacer_start == 20


async def test_protospacer_with_n_bases_is_excluded() -> None:
    # 5' filler with N's; PAM at known position; protospacer would contain N's.
    target = ("N" * 10) + _BALANCED_20NT.replace("ACGT", "ACNT", 1) + "AGG" + "T" * 17
    out = await design_guides(DesignGuidesInput(sequence=target, strands=["+"]))
    # Any returned guide must not contain N
    for g in out.guides:
        assert "N" not in g.protospacer


async def test_guides_sorted_by_heuristic_score_desc() -> None:
    # Construct a target with several PAM hits of varying quality.
    high_quality = "ACGTACGTACGTACGTACGT" + "AGG"  # balanced
    poly_t = ("TTTTTTTTTTACGTACGTAC") + "AGG"  # has polyT run → lower score
    target = ("A" * 5) + high_quality + ("A" * 10) + poly_t + ("A" * 5)
    out = await design_guides(DesignGuidesInput(sequence=target, strands=["+"]))
    scores = [g.score.heuristic_score for g in out.guides]
    assert scores == sorted(scores, reverse=True)


async def test_max_guides_caps_response() -> None:
    # Multiple NGG sites separated by enough room for distinct protospacers
    target = ("A" * 25 + "AGG") * 5
    out = await design_guides(DesignGuidesInput(sequence=target, max_guides=2))
    assert len(out.guides) <= 2
    assert out.num_candidates_total >= 2


async def test_custom_pam_iupac_codes() -> None:
    """Cas12a uses TTTV (V = A/C/G). Validator should accept it; scanner should match
    TTTA / TTTC / TTTG but NOT TTTT."""
    # Cas12a PAM is 5' of the protospacer, but for this test we just verify the PAM
    # regex compiles and matches; the position-relative-to-protospacer convention is the
    # tool's responsibility but currently treats PAM as 3' (Cas9-style). This test only
    # confirms IUPAC ambiguity handling.
    target = "A" * 25 + "TTTA" + "C" * 5  # TTTA matches TTTV
    out = await design_guides(DesignGuidesInput(sequence=target, pam="TTTV", strands=["+"]))
    assert out.num_returned >= 1
    assert out.guides[0].pam_sequence in ("TTTA", "TTTC", "TTTG")


async def test_rejects_short_sequence() -> None:
    with pytest.raises(pydantic.ValidationError):
        DesignGuidesInput(sequence="ATGCATGC")  # < 23 nt


async def test_rejects_non_dna() -> None:
    with pytest.raises(pydantic.ValidationError, match="non-DNA"):
        DesignGuidesInput(sequence="A" * 25 + "Z")


async def test_rejects_invalid_pam_chars() -> None:
    with pytest.raises(pydantic.ValidationError, match="unsupported characters"):
        DesignGuidesInput(sequence="A" * 25, pam="NGQ")  # Q not IUPAC


async def test_output_carries_explicit_notes_about_what_tool_is_not() -> None:
    """The agent must always be told this is heuristic ranking, not Doench scoring."""
    out = await design_guides(DesignGuidesInput(sequence=_PROBLEM_FREE_TARGET))
    notes_text = " ".join(out.notes).lower()
    assert "doench" in notes_text or "heuristic" in notes_text
    assert "off-target" in notes_text


async def test_is_registered_with_crispr_tag() -> None:
    from bioforge.tools.registry import get_tool

    spec = get_tool("design_guides")
    assert spec.cost_hint == "cheap"
    assert "crispr" in spec.tags
    assert spec.destructive is False


# --- on_target_score integration (compute_on_target_score=True) -----------------------


async def test_on_target_score_not_populated_by_default() -> None:
    """Default behavior: compute_on_target_score=False → on_target_score field is None."""
    out = await design_guides(DesignGuidesInput(sequence=_PROBLEM_FREE_TARGET, strands=["+"]))
    assert all(g.on_target_score is None for g in out.guides)
    # Note: the default notes DO mention the `compute_on_target_score=True` upgrade
    # path — that's intentional UX, not a leak of computed-when-not-asked behavior.


async def test_compute_on_target_score_populates_field() -> None:
    out = await design_guides(
        DesignGuidesInput(sequence=_PROBLEM_FREE_TARGET, strands=["+"], compute_on_target_score=True)
    )
    assert out.num_returned >= 1
    for g in out.guides:
        assert g.on_target_score is not None
        assert 0.0 <= g.on_target_score <= 1.0


async def test_compute_on_target_score_changes_ranking() -> None:
    """When on_target_score is computed, guides may rank differently than by
    heuristic_score alone. We don't assert a specific order — just that the sort
    key is on_target_score (with heuristic_score tiebreaker) when populated."""
    out = await design_guides(
        DesignGuidesInput(sequence=_PROBLEM_FREE_TARGET, strands=["+"], compute_on_target_score=True)
    )
    scores = [g.on_target_score for g in out.guides]
    # All non-None and sorted descending
    assert all(s is not None for s in scores)
    assert scores == sorted(scores, reverse=True)


async def test_compute_on_target_score_with_non_20nt_guide_emits_note() -> None:
    """Non-20-nt guides aren't supported by score_guide_on_target — must skip cleanly."""
    out = await design_guides(
        DesignGuidesInput(
            sequence=_PROBLEM_FREE_TARGET,
            strands=["+"],
            guide_length=18,
            compute_on_target_score=True,
        )
    )
    assert all(g.on_target_score is None for g in out.guides)
    note_text = " ".join(out.notes).lower()
    assert "20-nt" in note_text or "20 nt" in note_text or "ignored" in note_text


async def test_notes_mention_on_target_when_computed() -> None:
    out = await design_guides(
        DesignGuidesInput(sequence=_PROBLEM_FREE_TARGET, strands=["+"], compute_on_target_score=True)
    )
    note_text = " ".join(out.notes).lower()
    assert "on_target_score" in note_text or "on-target" in note_text
    assert "rule set 2" in note_text  # explicit non-claim


async def test_notes_always_mention_off_target_separation() -> None:
    """Whether or not on_target_score is computed, the notes must direct the user to
    `find_offtargets` for specificity — that's a separate concern."""
    out = await design_guides(DesignGuidesInput(sequence=_PROBLEM_FREE_TARGET, strands=["+"]))
    text = " ".join(out.notes).lower()
    assert "find_offtargets" in text or "off-target" in text
