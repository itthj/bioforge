"""Reliability (calibration) curve -- unit tests. numpy only, fully deterministic."""

from __future__ import annotations

import pytest
from bioforge.benchmarks.on_target_efficiency import GuidePrediction
from bioforge.benchmarks.reliability import reliability_curve, reliability_from_pairs


def test_monotone_curve_ranks_perfectly() -> None:
    curve = reliability_curve([(float(i), float(i)) for i in range(20)], n_bins=10)
    assert curve.n == 20
    assert curve.n_bins == 10
    assert sum(b.n for b in curve.bins) == 20  # every point accounted for
    # predicted_mean strictly increasing across bins; observed tracks it -> rho ~ 1.0.
    preds = [b.predicted_mean for b in curve.bins]
    assert preds == sorted(preds)
    assert curve.monotonicity_rho == pytest.approx(1.0)
    assert curve.kind == "regression_ranking"


def test_inverse_curve_is_anti_monotone() -> None:
    curve = reliability_curve([(float(i), float(-i)) for i in range(20)], n_bins=5)
    assert curve.monotonicity_rho == pytest.approx(-1.0)
    observed = [b.observed_mean for b in curve.bins]
    assert observed == sorted(observed, reverse=True)


def test_uneven_bins_account_for_all_points() -> None:
    curve = reliability_curve([(float(i), float(i % 3)) for i in range(23)], n_bins=10)
    assert curve.n == 23
    assert curve.n_bins == 10
    assert sum(b.n for b in curve.bins) == 23  # 23 split into 10 near-equal bins, nothing dropped


def test_observed_sem_zero_for_constant_bin() -> None:
    # n=4, n_bins=2: bin0 observed [5,5] -> sem 0; bin1 observed [9,11] -> mean 10, sem 1.0.
    curve = reliability_curve([(1.0, 5.0), (2.0, 5.0), (3.0, 9.0), (4.0, 11.0)], n_bins=2)
    assert curve.bins[0].observed_mean == pytest.approx(5.0)
    assert curve.bins[0].observed_sem == pytest.approx(0.0)
    assert curve.bins[1].observed_mean == pytest.approx(10.0)
    assert curve.bins[1].observed_sem == pytest.approx(1.0)
    assert curve.bins[0].predicted_low == 1.0 and curve.bins[0].predicted_high == 2.0


def test_min_points_and_bin_clamp() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        reliability_curve([(1.0, 1.0)], n_bins=10)
    # n=3 with n_bins=10 clamps to 3 single-point bins.
    curve = reliability_curve([(1.0, 1.0), (2.0, 2.0), (3.0, 3.0)], n_bins=10)
    assert curve.n_bins == 3
    assert all(b.n == 1 for b in curve.bins)


def test_caveat_disclaims_probability_calibration() -> None:
    curve = reliability_curve([(float(i), float(i)) for i in range(10)])
    assert "not a probability" in curve.caveat.lower()
    assert "y=x is not the target" in curve.caveat.lower()


def test_bad_shape_raises() -> None:
    with pytest.raises(ValueError, match="pairs"):
        reliability_curve([1.0, 2.0, 3.0])  # type: ignore[list-item]


def test_reliability_from_prediction_pairs() -> None:
    preds = [GuidePrediction(guide=f"g{i}", predicted=float(i), observed=float(i)) for i in range(12)]
    curve = reliability_from_pairs(preds, n_bins=4)
    assert curve.n == 12
    assert curve.n_bins == 4
    assert curve.monotonicity_rho == pytest.approx(1.0)
