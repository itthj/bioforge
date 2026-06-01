"""§6 / rule 11 -- reliability (calibration) curves from (predicted, observed) pairs.

A reliability curve is the evidence behind any displayed confidence: bin predictions, then show
the mean OBSERVED outcome per bin against the mean PREDICTED value. The §13 on-target benchmark
emits exactly these `(predicted, observed)` pairs, so this is the first real curve the platform
can draw.

HONESTY (rule 11, §6). The on-target score is NOT a probability, so this is **not** a
probability-calibration diagram and the y=x line is **not** the target -- predicted score and
measured efficiency live on different scales. What this curve shows is *ranking reliability*:
whether higher predicted scores correspond to higher measured efficiency, and where that
relationship saturates or breaks down. `kind="regression_ranking"` and the caveat say so, so the
UI can never imply a calibrated probability that the model does not emit.

numpy only (no scipy). Equal-count (quantile) bins, so a skewed score distribution still yields
populated bins.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

from bioforge.benchmarks.on_target_efficiency import GuidePrediction, spearman_rho

ReliabilityKind = Literal["regression_ranking", "probability_calibration"]

_NOT_PROBABILITY_CAVEAT = (
    "Ranking-reliability curve for a regression score: x is the mean predicted score per bin, y is "
    "the mean MEASURED outcome. The score is not a probability, so this is not a probability "
    "calibration diagram and y=x is not the target -- read it for whether higher scores track higher "
    "measured outcomes (monotonicity), not for absolute agreement."
)


class ReliabilityBin(BaseModel):
    """One quantile bin of predictions and the mean outcome observed within it."""

    bin_index: int
    n: int
    predicted_mean: float
    observed_mean: float
    observed_sem: float = Field(description="Standard error of the mean observed outcome in this bin (0 when n<2).")
    predicted_low: float = Field(description="Lowest predicted value in the bin (left edge).")
    predicted_high: float = Field(description="Highest predicted value in the bin (right edge).")


class ReliabilityCurve(BaseModel):
    """A binned predicted-vs-observed reliability curve plus its honesty framing."""

    n: int
    n_bins: int
    bins: list[ReliabilityBin]
    monotonicity_rho: float = Field(
        description="Spearman rho between per-bin predicted_mean and observed_mean; ~1.0 = reliably ranking.",
    )
    kind: ReliabilityKind
    predicted_label: str
    observed_label: str
    caveat: str


def reliability_curve(
    pairs: Sequence[tuple[float, float]],
    *,
    n_bins: int = 10,
    predicted_label: str = "predicted score",
    observed_label: str = "measured outcome",
    kind: ReliabilityKind = "regression_ranking",
) -> ReliabilityCurve:
    """Bin `(predicted, observed)` pairs by predicted value and summarize each bin.

    Equal-count bins: the pairs are sorted by predicted value and split into `n_bins` near-equal
    groups (so every bin is populated even for a skewed score). `n_bins` is clamped to the number
    of points. Raises on fewer than 2 points (a curve needs at least two).
    """
    arr = np.asarray(pairs, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != 2:
        raise ValueError("reliability_curve expects a sequence of (predicted, observed) pairs.")
    n = arr.shape[0]
    if n < 2:
        raise ValueError(f"reliability_curve needs at least 2 points; got {n}.")
    bins_target = max(1, min(n_bins, n))

    order = np.argsort(arr[:, 0], kind="mergesort")
    sorted_arr = arr[order]

    bins: list[ReliabilityBin] = []
    for bin_index, positions in enumerate(np.array_split(np.arange(n), bins_target)):
        block = sorted_arr[positions]
        preds = block[:, 0]
        obs = block[:, 1]
        count = int(block.shape[0])
        sem = float(np.std(obs, ddof=1) / np.sqrt(count)) if count > 1 else 0.0
        bins.append(
            ReliabilityBin(
                bin_index=bin_index,
                n=count,
                predicted_mean=float(preds.mean()),
                observed_mean=float(obs.mean()),
                observed_sem=sem,
                predicted_low=float(preds[0]),
                predicted_high=float(preds[-1]),
            )
        )

    rho = (
        spearman_rho([b.predicted_mean for b in bins], [b.observed_mean for b in bins])
        if len(bins) >= 2
        else float("nan")
    )

    return ReliabilityCurve(
        n=n,
        n_bins=len(bins),
        bins=bins,
        monotonicity_rho=round(rho, 4),
        kind=kind,
        predicted_label=predicted_label,
        observed_label=observed_label,
        caveat=_NOT_PROBABILITY_CAVEAT,
    )


def reliability_from_pairs(
    pairs: Sequence[GuidePrediction],
    **kwargs: object,
) -> ReliabilityCurve:
    """Convenience: build a curve straight from a benchmark result's `GuidePrediction` pairs."""
    return reliability_curve([(p.predicted, p.observed) for p in pairs], **kwargs)  # type: ignore[arg-type]
