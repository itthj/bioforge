"""Probability calibration metrics: ECE / MCE / Brier + a reliability diagram (§6, §13).

This is the primitive the reliability module deliberately did NOT provide. `reliability.py`
draws a *ranking* curve for a regression SCORE (monotonicity, not absolute agreement) and is
explicit that y=x is not its target. Calibration is the other half: when a model emits a genuine
PROBABILITY in [0, 1] paired with a BINARY outcome in {0, 1}, you can ask the stronger question
-- does a predicted 0.8 actually come true 80% of the time? -- and y=x IS the target.

What this computes (textbook, numpy-only):
  * **Reliability bins.** Predictions binned (equal-width over [0,1] by default, or equal-count),
    each bin's mean predicted probability vs the empirical outcome frequency.
  * **ECE** -- Expected Calibration Error: the sample-weighted mean |confidence - accuracy| across
    bins. The single number most papers report.
  * **MCE** -- Maximum Calibration Error: the worst bin's gap (the tail-risk view).
  * **Brier score** -- mean squared error of the probability against the 0/1 outcome. Bin-free,
    so it is a proper scoring rule independent of the binning choice.

HONESTY RAILS (rule 18, §0). This refuses to invent agreement:
  * Probabilities outside [0, 1] or outcomes not in {0, 1} RAISE -- never silently clamped (a
    score read as a probability it is not is exactly the failure this guards against).
  * `kind` records whether the inputs are a true probability ("probability") or were squashed from
    a score ("squashed_score") so a caller can never imply calibration the model does not support.
  * Empty / single-point input raises -- a calibration claim needs evidence.

The class mirrors ReliabilityCurve's shape so the frontend can render either with one component.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

CalibrationKind = Literal["probability", "squashed_score"]
BinningStrategy = Literal["equal_width", "equal_count"]

_PROBABILITY_CAVEAT = (
    "Probability-calibration diagram: x is the mean predicted probability per bin, y is the "
    "empirical outcome frequency. y=x IS the target here (unlike the regression-ranking curve). "
    "ECE is the sample-weighted mean gap; Brier is the bin-free proper score. A well-calibrated "
    "model tracks the diagonal; systematic deviation is over- or under-confidence."
)


class CalibrationBin(BaseModel):
    """One bin of predictions and the empirical outcome frequency within it."""

    bin_index: int
    n: int
    predicted_mean: float = Field(description="Mean predicted probability of points in the bin (the bin's confidence).")
    observed_freq: float = Field(description="Fraction of points in the bin whose outcome was 1 (the bin's accuracy).")
    gap: float = Field(description="|predicted_mean - observed_freq| -- the bin's calibration error.")
    bin_low: float = Field(description="Left edge of the bin (predicted-probability space).")
    bin_high: float = Field(description="Right edge of the bin.")


class CalibrationCurve(BaseModel):
    """A binned probability-vs-frequency reliability diagram plus its summary scores."""

    n: int
    n_bins: int
    bins: list[CalibrationBin]
    ece: float = Field(
        description="Expected Calibration Error: sample-weighted mean per-bin gap. Lower is better; 0 = perfect."
    )
    mce: float = Field(description="Maximum Calibration Error: the worst bin's gap.")
    brier: float = Field(
        description="Brier score: mean squared error of probability vs 0/1 outcome (bin-free). Lower is better."
    )
    base_rate: float = Field(
        description="Overall fraction of positive outcomes (the no-skill reference for the Brier)."
    )
    kind: CalibrationKind
    predicted_label: str
    observed_label: str
    caveat: str


def _validate_pairs(pairs: Sequence[tuple[float, float]]) -> np.ndarray:
    arr = np.asarray(pairs, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise ValueError("calibration_curve expects a sequence of (probability, outcome) pairs.")
    if arr.shape[0] < 2:
        raise ValueError(f"calibration_curve needs at least 2 points; got {arr.shape[0]}.")
    probs = arr[:, 0]
    outcomes = arr[:, 1]
    if np.any(probs < 0.0) or np.any(probs > 1.0):
        raise ValueError(
            "Predicted probabilities must lie in [0, 1]. A raw score is not a probability -- "
            "squash it explicitly (and pass kind='squashed_score') rather than feeding it here."
        )
    if not np.all(np.isin(outcomes, (0.0, 1.0))):
        raise ValueError("Outcomes must be binary (0 or 1). Calibration is defined against a binary ground truth.")
    return arr


def calibration_curve(
    pairs: Sequence[tuple[float, float]],
    *,
    n_bins: int = 10,
    strategy: BinningStrategy = "equal_width",
    kind: CalibrationKind = "probability",
    predicted_label: str = "predicted probability",
    observed_label: str = "observed frequency",
) -> CalibrationCurve:
    """Build a calibration curve + ECE / MCE / Brier from `(probability, outcome)` pairs.

    Args:
        pairs: (predicted_probability in [0,1], outcome in {0,1}).
        n_bins: number of reliability bins.
        strategy: "equal_width" (fixed [0,1] bins -- the standard ECE binning) or "equal_count"
            (quantile bins -- every bin populated, better for skewed confidences).
        kind: "probability" for a genuine probability; "squashed_score" if a score was mapped into
            [0,1] (the caveat then says so). Honesty marker only -- the math is identical.

    Equal-width empty bins are dropped from `bins` but their (zero) weight does not affect ECE.
    """
    arr = _validate_pairs(pairs)
    probs = arr[:, 0]
    outcomes = arr[:, 1]
    n = int(arr.shape[0])

    brier = float(np.mean((probs - outcomes) ** 2))
    base_rate = float(np.mean(outcomes))

    bins: list[CalibrationBin] = []
    if strategy == "equal_width":
        edges = np.linspace(0.0, 1.0, n_bins + 1)
        # np.digitize with right=False puts p==1.0 into an out-of-range bin; clamp the top edge.
        idx = np.clip(np.digitize(probs, edges[1:-1], right=False), 0, n_bins - 1)
        for b in range(n_bins):
            mask = idx == b
            count = int(np.count_nonzero(mask))
            if count == 0:
                continue
            p_mean = float(np.mean(probs[mask]))
            o_freq = float(np.mean(outcomes[mask]))
            bins.append(
                CalibrationBin(
                    bin_index=b,
                    n=count,
                    predicted_mean=p_mean,
                    observed_freq=o_freq,
                    gap=abs(p_mean - o_freq),
                    bin_low=float(edges[b]),
                    bin_high=float(edges[b + 1]),
                )
            )
    else:  # equal_count
        order = np.argsort(probs, kind="mergesort")
        sorted_p = probs[order]
        sorted_o = outcomes[order]
        bins_target = max(1, min(n_bins, n))
        for b, positions in enumerate(np.array_split(np.arange(n), bins_target)):
            block_p = sorted_p[positions]
            block_o = sorted_o[positions]
            count = int(block_p.shape[0])
            p_mean = float(np.mean(block_p))
            o_freq = float(np.mean(block_o))
            bins.append(
                CalibrationBin(
                    bin_index=b,
                    n=count,
                    predicted_mean=p_mean,
                    observed_freq=o_freq,
                    gap=abs(p_mean - o_freq),
                    bin_low=float(block_p[0]),
                    bin_high=float(block_p[-1]),
                )
            )

    # ECE = sample-weighted mean gap; MCE = worst gap. Weights sum to n across populated bins.
    ece = float(sum(b.n * b.gap for b in bins) / n)
    mce = float(max((b.gap for b in bins), default=0.0))

    return CalibrationCurve(
        n=n,
        n_bins=len(bins),
        bins=bins,
        ece=round(ece, 6),
        mce=round(mce, 6),
        brier=round(brier, 6),
        base_rate=round(base_rate, 6),
        kind=kind,
        predicted_label=predicted_label,
        observed_label=observed_label,
        caveat=_PROBABILITY_CAVEAT,
    )


def phred_to_probability(qual: float) -> float:
    """Convert a PHRED-scaled QUAL to P(call is real) = 1 - 10^(-QUAL/10).

    The VCF QUAL field is -10*log10 P(no variant at this site); the complement is the caller's
    probability the variant IS real. QUAL is clamped at 0 (negative PHRED is meaningless) and the
    result is bounded to [0, 1].
    """
    if qual < 0:
        qual = 0.0
    p = 1.0 - 10.0 ** (-qual / 10.0)
    return float(min(1.0, max(0.0, p)))
