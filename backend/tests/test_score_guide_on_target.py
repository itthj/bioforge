"""Tests for score_guide_on_target.

These verify the SCORING SEMANTICS (relative ordering of guides with known design
weaknesses/strengths), not absolute numbers. We deliberately don't pin specific score
values — the weights might be refined later, and the tests should keep passing as long
as the relative ranking holds.
"""

from __future__ import annotations

import pydantic
import pytest
from bioforge.tools.sequence.score_guide_on_target import (
    ScoreGuideOnTargetInput,
    score_guide_on_target,
)

# --- Component-level semantics -------------------------------------------------------


async def test_optimal_guide_scores_high() -> None:
    """A guide with ~50% GC, no polyT, GG at positions 19-20, no extreme features."""
    out = await score_guide_on_target(
        ScoreGuideOnTargetInput(protospacer="AGCTACGTACGTACGTACGG")
    )
    # Should score well above the neutral 0.5 baseline
    assert out.on_target_score >= 0.7
    assert out.score_breakdown.gc_component == 1.0  # 50% GC
    assert out.score_breakdown.polyt_component == 1.0  # no polyT


async def test_polyt_run_zeroes_polyt_component() -> None:
    # 4 T's in a row → polyt_component → 0
    out = await score_guide_on_target(
        ScoreGuideOnTargetInput(protospacer="ACGACGTTTTACGACGACGG")
    )
    assert out.score_breakdown.polyt_component == 0.0


async def test_extreme_gc_drops_gc_component() -> None:
    # 100% GC
    all_gc = await score_guide_on_target(
        ScoreGuideOnTargetInput(protospacer="GCGCGCGCGCGCGCGCGCGC")
    )
    # 100% GC is outside the optimal 40-60% range and outside the ramp-down zone
    assert all_gc.score_breakdown.gc_component == 0.0


async def test_low_gc_drops_gc_component() -> None:
    # 0% GC (all A/T)
    no_gc = await score_guide_on_target(
        ScoreGuideOnTargetInput(protospacer="AAAAAAAAAAAAAAAAAAAA")
    )
    assert no_gc.score_breakdown.gc_component == 0.0


# --- Position-specific preferences (Doench 2014 Table S1) ----------------------------


async def test_position_20_g_outscores_position_20_t() -> None:
    """Position 20 (last before PAM) strongly prefers G; T scores worst at that position."""
    g_at_20 = await score_guide_on_target(
        ScoreGuideOnTargetInput(protospacer="ACGTACGTACGTACGTACGG")
    )
    t_at_20 = await score_guide_on_target(
        ScoreGuideOnTargetInput(protospacer="ACGTACGTACGTACGTACGT")
    )
    assert g_at_20.score_breakdown.position_component > t_at_20.score_breakdown.position_component


async def test_seed_region_g_c_outscores_t() -> None:
    """Positions 16-20 (seed) prefer G/C over A/T."""
    seed_gc = await score_guide_on_target(
        ScoreGuideOnTargetInput(protospacer="ACGTACGTACGTACGTGCGG")  # last 5 = GCGCG
    )
    seed_at = await score_guide_on_target(
        ScoreGuideOnTargetInput(protospacer="ACGTACGTACGTACGTATAT")  # last 5 = ATATT
    )
    assert seed_gc.score_breakdown.position_component > seed_at.score_breakdown.position_component


async def test_gg_at_positions_19_20_boosts_dinucleotide_component() -> None:
    gg_19_20 = await score_guide_on_target(
        ScoreGuideOnTargetInput(protospacer="ACGTACGTACGTACGTACGG")
    )
    tt_19_20 = await score_guide_on_target(
        ScoreGuideOnTargetInput(protospacer="ACGTACGTACGTACGTACTT")
    )
    assert gg_19_20.score_breakdown.dinucleotide_component > tt_19_20.score_breakdown.dinucleotide_component


# --- Score range / breakdown invariants ---------------------------------------------


async def test_score_in_unit_interval() -> None:
    for guide in [
        "AGCTACGTACGTACGTACGG",
        "AAAAAAAAAAAAAAAAAAAA",
        "GCGCGCGCGCGCGCGCGCGC",
        "TTTTACGTACGTACGTACGG",
    ]:
        out = await score_guide_on_target(ScoreGuideOnTargetInput(protospacer=guide))
        assert 0.0 <= out.on_target_score <= 1.0


async def test_breakdown_components_in_unit_interval() -> None:
    out = await score_guide_on_target(
        ScoreGuideOnTargetInput(protospacer="AGCTACGTACGTACGTACGG")
    )
    b = out.score_breakdown
    assert 0.0 <= b.gc_component <= 1.0
    assert 0.0 <= b.polyt_component <= 1.0
    assert 0.0 <= b.position_component <= 1.0
    assert 0.0 <= b.dinucleotide_component <= 1.0
    assert sum(b.component_weights.values()) == pytest.approx(1.0)


async def test_score_matches_weighted_breakdown() -> None:
    out = await score_guide_on_target(
        ScoreGuideOnTargetInput(protospacer="AGCTACGTACGTACGTACGG")
    )
    b = out.score_breakdown
    w = b.component_weights
    expected = (
        w["gc"] * b.gc_component
        + w["polyt"] * b.polyt_component
        + w["position"] * b.position_component
        + w["dinucleotide"] * b.dinucleotide_component
    )
    assert out.on_target_score == pytest.approx(expected, abs=1e-3)


# --- Honesty / caveats --------------------------------------------------------------


async def test_caveats_mention_not_rule_set_2() -> None:
    out = await score_guide_on_target(
        ScoreGuideOnTargetInput(protospacer="AGCTACGTACGTACGTACGG")
    )
    text = " ".join(out.caveats).lower()
    assert "rule set 2" in text
    assert "not" in text
    assert "off-target" in text  # mentions the off-target separation


async def test_tool_description_avoids_doench_score_naming() -> None:
    """The tool must NOT be named or described as 'doench_score' — that would imply
    Rule Set 2 fidelity we don't have."""
    from bioforge.tools.registry import get_tool

    spec = get_tool("score_guide_on_target")
    assert spec.name == "score_guide_on_target"
    desc_lower = spec.description.lower()
    # Doench should be CITED but the score should NOT be claimed as a Rule Set 2 prediction
    assert "doench" in desc_lower
    assert "not the doench 2016 rule set 2" in desc_lower


# --- Adversarial validation ----------------------------------------------------------


async def test_rejects_wrong_length() -> None:
    with pytest.raises(pydantic.ValidationError):
        ScoreGuideOnTargetInput(protospacer="ACGT")  # too short
    with pytest.raises(pydantic.ValidationError):
        ScoreGuideOnTargetInput(protospacer="A" * 25)  # too long


async def test_rejects_ambiguous_bases() -> None:
    """Position-specific scoring is undefined for N bases — refuse rather than fudge."""
    with pytest.raises(pydantic.ValidationError, match="non-DNA"):
        ScoreGuideOnTargetInput(protospacer="ACGTACGTACGTACGTACGN")


async def test_is_registered_with_cheap_cost() -> None:
    """No expensive computation, no network — must be cheap so the planner can call it
    freely without triggering the approval gate."""
    from bioforge.tools.registry import get_tool

    spec = get_tool("score_guide_on_target")
    assert spec.cost_hint == "cheap"
    assert "crispr" in spec.tags
    assert "scoring" in spec.tags
