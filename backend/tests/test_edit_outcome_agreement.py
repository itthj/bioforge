"""§13 edit-outcome distribution-agreement -- unit tests. numpy only, deterministic."""

from __future__ import annotations

import math

import pytest
from bioforge.benchmarks.edit_outcome_agreement import (
    _LEAKAGE,
    EditOutcomeAgreementResult,
    assess_leakage_edit_outcome,
    compare_distributions,
    jensen_shannon_divergence,
    total_variation_distance,
)

# --- distance metrics ----------------------------------------------------------------------------


def test_tvd_identical_is_zero() -> None:
    assert total_variation_distance({"a": 0.6, "b": 0.4}, {"a": 0.6, "b": 0.4}) == 0.0


def test_tvd_disjoint_is_one() -> None:
    # Disjoint support: all of p on 'a', all of q on 'b'.
    assert total_variation_distance({"a": 1.0}, {"b": 1.0}) == pytest.approx(1.0)


def test_tvd_known_value() -> None:
    # p = {a:0.7,b:0.3}; q = {a:0.4,b:0.6}. TVD = 0.5*(|0.3|+|-0.3|) = 0.3.
    assert total_variation_distance({"a": 0.7, "b": 0.3}, {"a": 0.4, "b": 0.6}) == pytest.approx(0.3)


def test_tvd_handles_missing_labels_as_zero_mass() -> None:
    # Missing labels are treated as zero mass, NOT as an error -- the union is the event space.
    # p over {a,b}, q only over {a,c} -- they DO overlap on 'a' (so TVD < 1).
    p = {"a": 0.6, "b": 0.4}
    q = {"a": 0.5, "c": 0.5}
    # union = {a,b,c}; |0.6-0.5|+|0.4-0|+|0-0.5| = 0.1 + 0.4 + 0.5 = 1.0; tvd = 0.5.
    assert total_variation_distance(p, q) == pytest.approx(0.5)


def test_jsd_identical_is_zero_and_disjoint_is_one() -> None:
    assert jensen_shannon_divergence({"a": 0.5, "b": 0.5}, {"a": 0.5, "b": 0.5}) == pytest.approx(0.0)
    # Disjoint support, JSD (base 2) maxes at 1.0.
    assert jensen_shannon_divergence({"a": 1.0}, {"b": 1.0}) == pytest.approx(1.0)


def test_jsd_symmetric() -> None:
    p = {"a": 0.7, "b": 0.3}
    q = {"a": 0.4, "b": 0.6}
    assert jensen_shannon_divergence(p, q) == pytest.approx(jensen_shannon_divergence(q, p))


# --- validation ----------------------------------------------------------------------------------


def test_distribution_must_sum_to_one() -> None:
    with pytest.raises(ValueError, match="sums to"):
        total_variation_distance({"a": 0.5, "b": 0.4}, {"a": 0.5, "b": 0.5})


def test_distribution_must_be_nonnegative() -> None:
    with pytest.raises(ValueError, match="invalid probability"):
        total_variation_distance({"a": 1.2, "b": -0.2}, {"a": 0.5, "b": 0.5})


def test_distribution_must_be_nonempty() -> None:
    with pytest.raises(ValueError, match="empty"):
        total_variation_distance({}, {"a": 1.0})


def test_distribution_rejects_nan() -> None:
    with pytest.raises(ValueError, match="invalid probability"):
        total_variation_distance({"a": math.nan, "b": 0.0}, {"a": 1.0})


# --- compare_distributions + honesty rails -------------------------------------------------------


def test_compare_distributions_typed_result_with_unknown_leakage() -> None:
    p = {"-2+4": 0.3, "+1": 0.2, "-3": 0.5}
    o = {"-2+4": 0.4, "+1": 0.1, "-3": 0.5}
    result = compare_distributions(p, o, dataset="lindel_demo", model="lindel", model_version="lindel-fdcad58")
    assert isinstance(result, EditOutcomeAgreementResult)
    assert result.n_labels == 3
    # tvd = 0.5 * (0.1 + 0.1 + 0) = 0.1; jsd is small but positive
    assert result.tvd == pytest.approx(0.1)
    assert 0.0 < result.jsd < 0.2
    # Honesty: leakage 'unknown' (no registry entry), framed as agreement not held-out claim
    assert result.leakage_status == "unknown"
    assert result.leakage_evidence == ""
    assert "agreement measurement" in result.interpretation.lower()
    assert "not a held-out accuracy claim" in result.interpretation.lower()
    # Per-label pairs preserved -- the same data the reliability diagram consumes.
    labels = {p.label for p in result.pairs}
    assert labels == {"-2+4", "+1", "-3"}


def test_compare_distributions_perfect_match_is_zero() -> None:
    p = {"a": 0.25, "b": 0.25, "c": 0.5}
    result = compare_distributions(p, p, dataset="d", model="m", model_version="v")
    assert result.tvd == 0.0
    assert result.jsd == 0.0


def test_leakage_registry_starts_empty_no_unsourced_held_out() -> None:
    """The integrity guard transferred from on-target / off-target: the registry starts EMPTY,
    so a 'held_out' label cannot appear in this module until someone adds a sourced entry."""
    assert _LEAKAGE == {}


def test_assess_leakage_default_unknown() -> None:
    a = assess_leakage_edit_outcome("nope", "lindel")
    assert a.status == "unknown"
    assert a.evidence == ""


def test_every_leakage_claim_is_sourced() -> None:
    """Structural guard (rule 18, §0): if and when entries are added, every 'held_out' /
    'contaminated' MUST carry primary-source evidence. A label-from-memory is impossible."""
    for (dataset, model), a in _LEAKAGE.items():
        if a.status in {"held_out", "contaminated"}:
            assert a.evidence, f"({dataset!r}, {model!r}) claims {a.status!r} without primary-source evidence"
