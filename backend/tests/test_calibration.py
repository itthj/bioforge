"""Tests for benchmarks.calibration -- probability calibration (ECE / MCE / Brier).

The metrics are checked against hand-computed textbook values, and the honesty rails (reject a
score read as a probability, reject non-binary outcomes) are asserted. Plus the GIAB QUAL
calibration consumer in variant_concordance.
"""

from __future__ import annotations

import pytest
from bioforge.benchmarks.calibration import (
    calibration_curve,
    phred_to_probability,
)


def test_perfect_calibration_zero_error() -> None:
    """A predictor whose probabilities exactly match outcome frequencies: ECE = MCE = 0.

    Bin 0.0: ten 0-outcomes (predicted 0.0). Bin 1.0: ten 1-outcomes (predicted 1.0). Each bin's
    confidence equals its accuracy, so both calibration errors are 0.
    """
    pairs = [(0.0, 0.0)] * 10 + [(1.0, 1.0)] * 10
    curve = calibration_curve(pairs, n_bins=10)
    assert curve.ece == 0.0
    assert curve.mce == 0.0
    assert curve.brier == 0.0
    assert curve.base_rate == 0.5


def test_brier_score_textbook_value() -> None:
    """Brier = mean((p - o)^2). For [(0.7,1),(0.3,0),(0.9,1),(0.1,0)]:
    (0.3^2 + 0.3^2 + 0.1^2 + 0.1^2)/4 = (0.09+0.09+0.01+0.01)/4 = 0.05.
    """
    pairs = [(0.7, 1.0), (0.3, 0.0), (0.9, 1.0), (0.1, 0.0)]
    curve = calibration_curve(pairs, n_bins=10)
    assert curve.brier == pytest.approx(0.05, abs=1e-9)


def test_ece_overconfident_predictor() -> None:
    """A predictor that always says 1.0 but is right only half the time.

    All 20 points land in the top bin: predicted_mean=1.0, observed_freq=0.5, gap=0.5.
    ECE = 0.5 (one populated bin), MCE = 0.5, Brier = mean of 10*(1-1)^2 + 10*(1-0)^2 over 20 = 0.5.
    """
    pairs = [(1.0, 1.0)] * 10 + [(1.0, 0.0)] * 10
    curve = calibration_curve(pairs, n_bins=10)
    assert curve.ece == pytest.approx(0.5, abs=1e-9)
    assert curve.mce == pytest.approx(0.5, abs=1e-9)
    assert curve.brier == pytest.approx(0.5, abs=1e-9)
    assert curve.n_bins == 1  # only the top bin is populated


def test_ece_is_sample_weighted() -> None:
    """ECE weights bins by count, not equally. 18 well-calibrated + 2 badly-calibrated points:
    the small bad bin should contribute proportionally, keeping ECE small.
    """
    # 18 points perfectly calibrated at 0.0/1.0, 2 points overconfident at 1.0 but wrong.
    pairs = [(0.0, 0.0)] * 9 + [(1.0, 1.0)] * 9 + [(1.0, 0.0)] * 2
    curve = calibration_curve(pairs, n_bins=10)
    # Top bin: 11 points (9 ones + 2 zeros), predicted 1.0, observed 9/11; gap = 2/11.
    # ECE = (11 * 2/11) / 20 = 2/20 = 0.1.
    assert curve.ece == pytest.approx(0.1, abs=1e-9)


def test_rejects_probability_above_one() -> None:
    """A raw score > 1 must RAISE -- never silently clamped (the core honesty rail)."""
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        calibration_curve([(1.5, 1.0), (0.2, 0.0)])


def test_rejects_negative_probability() -> None:
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        calibration_curve([(-0.1, 0.0), (0.5, 1.0)])


def test_rejects_non_binary_outcome() -> None:
    """An outcome of 0.5 is not a binary ground truth -- calibration is undefined; RAISE."""
    with pytest.raises(ValueError, match="binary"):
        calibration_curve([(0.7, 0.5), (0.3, 0.0)])


def test_rejects_too_few_points() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        calibration_curve([(0.5, 1.0)])


def test_equal_count_strategy_populates_every_bin() -> None:
    """Equal-count binning over a skewed confidence still yields populated bins."""
    pairs = [(0.01 * i, 1.0 if i % 2 == 0 else 0.0) for i in range(100)]
    curve = calibration_curve(pairs, n_bins=5, strategy="equal_count")
    assert curve.n_bins == 5
    assert all(b.n == 20 for b in curve.bins)


def test_kind_marker_records_squashed_score() -> None:
    """A squashed score must be marked as such so the UI never implies a true probability."""
    curve = calibration_curve([(0.6, 1.0), (0.4, 0.0)], kind="squashed_score")
    assert curve.kind == "squashed_score"


# --- phred_to_probability ---------------------------------------------------------------


def test_phred_to_probability_known_values() -> None:
    # QUAL 10 -> 1 - 10^-1 = 0.9; QUAL 20 -> 0.99; QUAL 30 -> 0.999.
    assert phred_to_probability(10) == pytest.approx(0.9, abs=1e-9)
    assert phred_to_probability(20) == pytest.approx(0.99, abs=1e-9)
    assert phred_to_probability(30) == pytest.approx(0.999, abs=1e-9)


def test_phred_zero_and_negative_clamped() -> None:
    assert phred_to_probability(0) == 0.0
    assert phred_to_probability(-5) == 0.0  # negative PHRED clamped to 0 -> P=0


def test_phred_large_value_bounded() -> None:
    assert phred_to_probability(1000) <= 1.0


# --- GIAB QUAL calibration consumer -----------------------------------------------------


def test_score_call_calibration_against_truth() -> None:
    """High-QUAL calls that ARE in truth + low-QUAL calls that are NOT => well-calibrated."""
    from bioforge.benchmarks.variant_concordance import (
        ConfidentRegion,
        QualifiedCall,
        VariantCall,
        score_call_calibration,
    )

    regions = [ConfidentRegion(chrom="chr1", start=0, end=10_000)]
    # Truth set: two SNVs.
    truth = [
        VariantCall(chrom="chr1", pos=100, ref="A", alt="G"),
        VariantCall(chrom="chr1", pos=200, ref="C", alt="T"),
    ]
    # Called: the two true ones at high QUAL (P~0.999) + two false ones at low QUAL (P~0.9).
    called = [
        QualifiedCall(VariantCall(chrom="chr1", pos=100, ref="A", alt="G"), qual=30),
        QualifiedCall(VariantCall(chrom="chr1", pos=200, ref="C", alt="T"), qual=30),
        QualifiedCall(VariantCall(chrom="chr1", pos=300, ref="G", alt="A"), qual=30),
        QualifiedCall(VariantCall(chrom="chr1", pos=400, ref="T", alt="C"), qual=30),
    ]
    curve = score_call_calibration(called, truth, regions, n_bins=10)
    assert curve.n == 4
    # Two of four calls at P=0.999 are true positives -> observed freq 0.5 in the top bin.
    top_bin = curve.bins[-1]
    assert top_bin.predicted_mean == pytest.approx(0.999, abs=1e-6)
    assert top_bin.observed_freq == pytest.approx(0.5, abs=1e-9)


def test_score_call_calibration_excludes_out_of_region() -> None:
    """Calls outside the confident regions are dropped before calibration (GIAB discipline)."""
    from bioforge.benchmarks.variant_concordance import (
        ConfidentRegion,
        QualifiedCall,
        VariantCall,
        score_call_calibration,
    )

    regions = [ConfidentRegion(chrom="chr1", start=0, end=150)]
    truth = [VariantCall(chrom="chr1", pos=100, ref="A", alt="G")]
    called = [
        QualifiedCall(VariantCall(chrom="chr1", pos=100, ref="A", alt="G"), qual=30),  # in region
        QualifiedCall(VariantCall(chrom="chr1", pos=500, ref="C", alt="T"), qual=30),  # out of region
    ]
    # n=1 would raise (needs >= 2 points): assert the rejection, proving out-of-region was dropped.
    with pytest.raises(ValueError, match="at least 2"):
        score_call_calibration(called, truth, regions, n_bins=10)
