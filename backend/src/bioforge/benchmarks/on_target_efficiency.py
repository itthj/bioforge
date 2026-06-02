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
from dataclasses import dataclass
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

from bioforge.benchmarks.effdata import DownloadFn, load_dataset
from bioforge.config import Settings
from bioforge.config import settings as _default_settings

# A scorer injected for tests / alternate models: 23-mers in -> one score per guide, same order.
PredictFn = Callable[[list[str]], Sequence[float]]

LeakageStatus = Literal["held_out", "unknown", "contaminated"]


@dataclass(frozen=True)
class LeakageAssessment:
    """A (dataset, model) leakage call that MUST carry its primary-source evidence.

    The whole platform stands on never claiming an accuracy a tool's own license / paper does
    not support (§0, rule 18). Leakage status is the most-loaded label this module emits -- a
    'held_out' claim with no source is exactly the kind of confident-wrong-number the platform
    exists to refuse. Hence the structural rule, enforced by `test_every_leakage_claim_is_sourced`:
    `status='unknown'` is the only value that may carry an empty `evidence` string; every
    `held_out` / `contaminated` claim must cite the paper + section + statement that grounds it.
    `caveat` records any residual concern the evidence does NOT fully close.
    """

    status: LeakageStatus
    evidence: str
    caveat: str = ""


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
            "Whether the eval set is in the model's training data. 'unknown' until a primary "
            "source resolves it -- 'held_out' / 'contaminated' MUST carry `leakage_evidence`."
        ),
    )
    leakage_evidence: str = Field(
        default="",
        description=(
            "Verbatim primary-source citation grounding the leakage status. Required for "
            "'held_out' / 'contaminated'; empty for 'unknown' (the platform never invents this)."
        ),
    )
    leakage_caveat: str = Field(
        default="",
        description="Any residual concern the evidence does NOT fully close (e.g. unverified guide overlap).",
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


# (dataset, model) -> a typed LeakageAssessment with PRIMARY-SOURCE evidence. Sourced 2026-06-01
# from the Chuai 2018 paper (Genome Biol 19:80) on PMC, not from memory. A new (dataset, model)
# pair without a verified entry defaults to ('unknown', '') and the platform refuses to claim
# 'held_out' for it -- see `assess_leakage`.
_LEAKAGE: dict[tuple[str, str], LeakageAssessment] = {
    ("chari2015Train", "deepcrispr"): LeakageAssessment(
        status="held_out",
        evidence=(
            "Chuai et al. 2018, DeepCRISPR (Genome Biology 19:80, PMC6020378). The on-target "
            "training corpus is ~15,000 sgRNAs across HCT116/HEK293T/HeLa/HL60 sourced from Wang "
            "2014, Hart 2015 and Doench 2016 (refs [2], [36], [37]). Chari 2015 (Nat Methods "
            "12:823) is cited as reference [12] and used ONLY as an independent validation set "
            "(Testing Scenario 8) -- NOT as a training source. Verified against the PMC full "
            "text 2026-06-01."
        ),
        caveat=(
            "Residual concern not closed by the evidence: the Chuai 2018 paper enumerates a "
            "'HEL cells, 425 sgRNAs' Chari-2015 cut for Testing Scenario 8, while crisporPaper's "
            "chari2015Train.tab is 1234 293T guides (Chari's own training split). The 293T cut "
            "was therefore likely seen by DeepCRISPR only as an independent benchmark too, but "
            "this is not stated verbatim. Incidental guide-sequence overlap with the Doench-2016 "
            "HEK293T training subset is also possible and has not been checked at the sequence "
            "level -- if it materially affects rho, the residual is small (different libraries)."
        ),
    ),
}

# (dataset, model) -> relationship. DeepCRISPR trained on Chuai 2018 cell-line screens; Chari-2015
# is a separate screen -> cross-dataset (Haeussler 2016: cross-dataset on-target rho is modest).
_RELATIONSHIP: dict[tuple[str, str], str] = {
    ("chari2015Train", "deepcrispr"): "cross_dataset",
}


def assess_leakage(dataset: str, model: str) -> LeakageAssessment:
    """Return the typed leakage assessment for (dataset, model), defaulting to ('unknown','').

    A missing entry is structurally `unknown` with empty evidence -- the platform never claims
    `held_out` for a pair without a recorded primary-source citation.
    """
    return _LEAKAGE.get((dataset, model), LeakageAssessment(status="unknown", evidence=""))


def _interpretation(rho: float, leakage: LeakageAssessment, relationship: str, dataset: str) -> str:
    parts = [f"Spearman rho={rho:.3f} between predicted score and MEASURED editing efficiency on {dataset}."]
    if relationship == "cross_dataset":
        parts.append(
            "This is a CROSS-DATASET evaluation: the model was trained on a different screen. "
            "Cross-dataset on-target correlations are known to be modest (Haeussler 2016), so a "
            "low-to-moderate rho is expected here and is not by itself evidence the scorer is broken."
        )
    if leakage.status == "held_out":
        parts.append(f"Held-out vs the model's training set. Evidence: {leakage.evidence}")
        if leakage.caveat:
            parts.append(f"Residual caveat: {leakage.caveat}")
    elif leakage.status == "unknown":
        parts.append(
            "Leakage status is UNKNOWN -- whether this eval set was in the model's training data "
            "has not been verified against a primary source -- so this is reported as a "
            "cross-dataset correlation, NOT a held-out accuracy claim."
        )
    elif leakage.status == "contaminated":
        parts.append(
            "WARNING: this eval set overlaps the model's training data; the rho is OPTIMISTIC. "
            f"Evidence: {leakage.evidence}"
        )
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
    leakage = assess_leakage(dataset, model)
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
        leakage_status=leakage.status,
        leakage_evidence=leakage.evidence,
        leakage_caveat=leakage.caveat,
        dataset_relationship=relationship,
        interpretation=_interpretation(rho, leakage, relationship, dataset),
        source=loaded.source,
        data_sha256=loaded.sha256,
        citation=loaded.spec.citation,
        pairs=pairs,
    )
