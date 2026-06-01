"""§13 on-target accuracy benchmark -- guide-efficiency rank correlation.

Measures how well an on-target scorer's predictions RANK-correlate with MEASURED guide-editing
efficiency on an independent screen. The metric is Spearman rho (rank correlation) plus Pearson r,
computed with numpy -- scipy is intentionally NOT a dependency.

Honesty is built into the result type (rule 18, §0). Every result carries:

  * `leakage_status` -- whether the eval set could have been in the model's TRAINING data. Until
    that is verified against the model's published training description this is "unknown", NEVER
    "held_out": presenting a possibly-contaminated rho as held-out accuracy would be exactly the
    confident-wrong-number the platform exists to refuse.
  * `dataset_relationship` -- DeepCRISPR was trained on the Chuai 2018 cell-line screens, so
    Chari-2015 is a CROSS-DATASET evaluation. Cross-dataset on-target correlations are known to be
    modest (Haeussler 2016); a low-to-moderate rho here is expected and is not, by itself,
    evidence the scorer is broken.
  * `pairs` -- the per-guide (predicted, observed) values. These are the inputs a calibration /
    reliability-diagram arm consumes (rule 11), so this benchmark also unblocks that downstream.

This module COMPUTES the metric. It is wired into the Accuracy Report as `guard_only`, not
`live`: a real run needs a network fetch plus an out-of-process DeepCRISPR Docker call, which must
never happen synchronously on a page load (the same reasoning that keeps ClinVar fidelity
guard_only). The numpy correlation + the loader are unit-tested; the real run is a nightly
`-m docker -m online` e2e.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

from bioforge.benchmarks.effdata import DownloadFn, load_dataset
from bioforge.config import Settings
from bioforge.config import settings as _default_settings

# A scorer injected for tests / alternate models: 23-mers in -> one score per guide, same order.
PredictFn = Callable[[list[str]], Sequence[float]]

LeakageStatus = Literal["held_out", "unknown", "contaminated"]


# --- numpy correlation (tie-aware; no scipy) ----------------------------------------------------


def average_ranks(values: Sequence[float]) -> np.ndarray:
    """Tie-aware average ranks (1-based), matching scipy.stats.rankdata(method='average').

    Tied values all receive the mean of the ranks they jointly occupy -- the standard correction
    for Spearman with ties. Uses a stable sort so the result is deterministic.
    """
    arr = np.asarray(values, dtype=float)
    n = arr.size
    order = arr.argsort(kind="mergesort")
    ranks = np.empty(n, dtype=float)
    sorted_arr = arr[order]
    i = 0
    while i < n:
        j = i
        while j + 1 < n and sorted_arr[j + 1] == sorted_arr[i]:
            j += 1
        # Positions i..j (0-based) occupy 1-based ranks (i+1)..(j+1); assign their average.
        ranks[order[i : j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return ranks


def pearson_r(x: Sequence[float], y: Sequence[float]) -> float:
    """Pearson correlation. Returns NaN when either input has zero variance (undefined)."""
    xa = np.asarray(x, dtype=float)
    ya = np.asarray(y, dtype=float)
    if xa.size != ya.size:
        raise ValueError(f"pearson_r length mismatch: {xa.size} vs {ya.size}")
    if xa.size < 2:
        raise ValueError("pearson_r needs at least 2 points")
    xc = xa - xa.mean()
    yc = ya - ya.mean()
    denom = np.sqrt(float((xc * xc).sum()) * float((yc * yc).sum()))
    if denom == 0.0:
        return float("nan")
    return float((xc * yc).sum() / denom)


def spearman_rho(x: Sequence[float], y: Sequence[float]) -> float:
    """Spearman rank correlation = Pearson on tie-aware average ranks."""
    return pearson_r(average_ranks(x), average_ranks(y))


# --- result type --------------------------------------------------------------------------------


class GuidePrediction(BaseModel):
    """One guide's (predicted, observed) pair -- the unit a calibration arm consumes (rule 11)."""

    guide: str
    predicted: float
    observed: float = Field(description="Measured modification frequency (upstream value, not rescaled).")


class OnTargetEfficiencyResult(BaseModel):
    """A scorer's measured agreement with observed guide efficiency, with its honesty labels."""

    dataset: str
    model: str
    model_version: str
    n: int = Field(description="Number of guides scored.")
    spearman_rho: float = Field(description="Rank correlation between predicted score and measured efficiency.")
    pearson_r: float
    leakage_status: LeakageStatus = Field(
        description=(
            "Whether the eval set could be in the model's training data. 'unknown' until verified "
            "against the model's published training description -- never assume 'held_out'."
        ),
    )
    dataset_relationship: str = Field(
        description="e.g. 'cross_dataset' -- the model was trained on a DIFFERENT screen than this eval set.",
    )
    interpretation: str = Field(description="Plain-language framing so a modest rho is read honestly, not as a defect.")
    source: str = Field(description="Provenance of the eval data (fetch URL / cache / supplied path).")
    data_sha256: str
    citation: str
    pairs: list[GuidePrediction] = Field(
        description="Per-guide (predicted, observed) pairs -- inputs to calibration / reliability diagrams.",
    )


# (dataset, model) -> leakage status. UNKNOWN until cross-checked against the model's published
# training datasets. DeepCRISPR == Chuai et al. 2018 (Genome Biol 19:80); whether the Chari-2015
# library overlapped its training screens is NOT yet confirmed, so we must not call it held_out.
# VERIFY: confirm against the Chuai 2018 training-data description before promoting to "held_out".
_LEAKAGE: dict[tuple[str, str], LeakageStatus] = {
    ("chari2015Train", "deepcrispr"): "unknown",
}

# (dataset, model) -> relationship. DeepCRISPR trained on Chuai 2018 cell-line screens; Chari-2015
# is a separate screen -> cross-dataset (Haeussler 2016: cross-dataset on-target rho is modest).
_RELATIONSHIP: dict[tuple[str, str], str] = {
    ("chari2015Train", "deepcrispr"): "cross_dataset",
}


def _interpretation(rho: float, leakage: LeakageStatus, relationship: str, dataset: str) -> str:
    parts = [f"Spearman rho={rho:.3f} between predicted score and MEASURED editing efficiency on {dataset}."]
    if relationship == "cross_dataset":
        parts.append(
            "This is a CROSS-DATASET evaluation: the model was trained on a different screen. "
            "Cross-dataset on-target correlations are known to be modest (Haeussler 2016), so a "
            "low-to-moderate rho is expected here and is not by itself evidence the scorer is broken."
        )
    if leakage == "unknown":
        parts.append(
            "Leakage status is UNKNOWN -- whether this eval set was in the model's training data "
            "has not been verified -- so this is reported as a cross-dataset correlation, NOT a "
            "held-out accuracy claim."
        )
    elif leakage == "contaminated":
        parts.append("WARNING: this eval set overlaps the model's training data; the rho is optimistic.")
    return " ".join(parts)


def run_on_target_efficiency(
    dataset: str = "chari2015Train",
    model: str = "deepcrispr",
    *,
    settings: Settings | None = None,
    download_fn: DownloadFn | None = None,
    local_path: str | None = None,
    predict_fn: PredictFn | None = None,
) -> OnTargetEfficiencyResult:
    """Score every guide in `dataset` with `model` and correlate against measured efficiency.

    The eval data is fetched-on-first-use (or read from `local_path`), sha256-verified. Scoring
    uses the real DeepCRISPR backend by default (opt-in, out-of-process); tests inject `predict_fn`
    so the metric path is exercised without Docker. The result carries its honesty labels (leakage,
    cross-dataset relationship) and the per-guide (predicted, observed) pairs.
    """
    s = settings if settings is not None else _default_settings
    loaded = load_dataset(dataset, settings=s, download_fn=download_fn, local_path=local_path)
    seqs = [row.seq for row in loaded.rows]
    observed = [row.mod_freq for row in loaded.rows]

    if predict_fn is not None:
        scores = list(predict_fn(seqs))
        model_version = f"{model}:injected"
    elif model == "deepcrispr":
        from bioforge.tools.sequence.models.deepcrispr import predict_on_target

        result = predict_on_target(seqs, settings=s)
        scores = [sc.score for sc in result.scores]
        model_version = result.model_version
    else:
        raise ValueError(
            f"Unsupported on-target model {model!r}. Wire it like 'deepcrispr', or pass predict_fn "
            "to inject a scorer (23-mers -> scores)."
        )

    if len(scores) != len(observed):
        raise ValueError(f"Scorer returned {len(scores)} scores for {len(observed)} guides.")

    rho = spearman_rho(scores, observed)
    r = pearson_r(scores, observed)
    leakage = _LEAKAGE.get((dataset, model), "unknown")
    relationship = _RELATIONSHIP.get((dataset, model), "cross_dataset")
    pairs = [
        GuidePrediction(guide=row.guide_name, predicted=float(score), observed=row.mod_freq)
        for row, score in zip(loaded.rows, scores, strict=True)
    ]

    return OnTargetEfficiencyResult(
        dataset=dataset,
        model=model,
        model_version=model_version,
        n=len(observed),
        spearman_rho=round(rho, 4),
        pearson_r=round(r, 4),
        leakage_status=leakage,
        dataset_relationship=relationship,
        interpretation=_interpretation(rho, leakage, relationship, dataset),
        source=loaded.source,
        data_sha256=loaded.sha256,
        citation=loaded.spec.citation,
        pairs=pairs,
    )
