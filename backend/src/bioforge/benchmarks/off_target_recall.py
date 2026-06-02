"""§13 off-target recall benchmark -- how well CFD ranks validated off-target sites.

For each (sgRNA, validated off-target site) pair from a crisporPaper aggregated off-target
table (Tsai 2015 GUIDE-seq / Frock 2015 HTGTS / Cho 2014 / Kim 2015 Digenome-seq / ...),
compute the Doench-2016 **CFD** score from the platform's own implementation and correlate it
with the upstream `readFraction` (per-sgRNA-normalized experimental signal strength).

What this measures: **score discrimination** -- whether high CFD calls correspond to high
experimental readout. That's the building block of the blueprint's off-target recall metric.
Recall@quantile-of-CFD is also returned (top 10% / 25% / 50% of CFD recovers what fraction of the
strongest sites), which is what a wet-lab triage actually consumes.

HONESTY rails (same design as on-target):
- Leakage status is `unknown` until verified against Doench 2016's primary source (the CFD
  matrices were trained on a synthetic mismatched-library screen; whether any of the Tsai/Frock/
  Cho/Kim ENDOGENOUS validated sites overlapped that screen is not yet checked). A future slice
  promotes this with evidence -- the same gate that closed Chari/DeepCRISPR.
- `pairs` carries the per-site `(cfd, readFraction)` -- the calibration / reliability-diagram
  inputs (rule 11). The reliability component already accepts these, so the same UI surface
  visualises off-target discrimination at zero extra cost.
- No silent data: `cfd_score` (the full Doench 2016 CFD, mismatch x PAM) is called on the 20 nt
  protospacer + 2 nt PAM peeled from the 23-mer, exactly as Doench's reference implementation
  does. Pairs that fail length/PAM validation are dropped with a recorded `n_skipped` so a
  malformed input is never silently scored as zero.

numpy only.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

from bioforge.benchmarks.effdata import DownloadFn, EffOfftargetRow, load_dataset
from bioforge.benchmarks.on_target_efficiency import (
    LeakageAssessment,
    LeakageStatus,
    pearson_r,
    spearman_rho,
)
from bioforge.config import Settings
from bioforge.config import settings as _default_settings
from bioforge.tools.sequence.offtarget_scoring import cfd_score

# Test injection point: (guide_20nt, ot_20nt, pam_2nt) -> CFD-like score.
ScoreFn = Callable[[str, str, str], float]


class OfftargetPair(BaseModel):
    """One (predicted CFD, observed readFraction) pair plus the guide it came from."""

    guide_name: str
    cfd: float
    read_fraction: float
    mismatches: int


class RecallAtQuantile(BaseModel):
    """Of the top `q` quantile of sites by CFD, what fraction of the strongest-signal sites
    (top `q` quantile by readFraction) does it recover? A score that ranks perfectly returns 1.0.
    """

    quantile: float
    recall: float


class OffTargetRecallResult(BaseModel):
    """CFD's measured discrimination on a validated-site corpus, with honesty labels."""

    dataset: str
    model: Literal["cfd_full"]
    model_version: str
    n: int
    n_skipped: int = Field(description="Pairs dropped for malformed input (bad length / non-NGG PAM).")
    spearman_rho: float = Field(description="Rank correlation between CFD and readFraction.")
    pearson_r: float
    recall_at: list[RecallAtQuantile]
    leakage_status: LeakageStatus
    leakage_evidence: str = ""
    leakage_caveat: str = ""
    interpretation: str
    source: str
    data_sha256: str
    citation: str
    pairs: list[OfftargetPair]


# Same evidence-trail discipline as on-target: structurally impossible to claim 'held_out' from
# memory. Doench 2016's CFD training set was a synthetic mismatched-target library; whether the
# Tsai/Frock/Cho/Kim endogenous validated sites overlapped it is NOT yet verified, so 'unknown'.
# Promote with primary-source evidence in a follow-up (the assess_leakage gate enforces this).
_LEAKAGE: dict[tuple[str, str], LeakageAssessment] = {
    ("annotOfftargets", "cfd_full"): LeakageAssessment(
        status="unknown",
        evidence="",
        caveat=(
            "CFD's mismatch matrices come from Doench 2016's synthetic mismatched-library screen "
            "(HEK293/A375); the eval sites here are endogenous off-targets from Tsai/Frock/Cho/Kim "
            "screens, which are a different experimental regime -- almost certainly held-out in "
            "practice, but not promoted until a Doench-2016 primary-source check confirms it."
        ),
    ),
}


def assess_leakage_offtarget(dataset: str, model: str) -> LeakageAssessment:
    """Return the typed leakage assessment for (dataset, model), defaulting to ('unknown','')."""
    return _LEAKAGE.get((dataset, model), LeakageAssessment(status="unknown", evidence=""))


def _peel_protospacer_and_pam(seq23: str) -> tuple[str, str] | None:
    """A 23-mer `NNNNNNNNNNNNNNNNNNNN NGG` -> (20 nt protospacer, 2 nt PAM=`GG`-like).

    Returns None on bad length / non-ACGT bases / non-NGG PAM (the row is then skipped, not
    silently scored). Doench's convention scores the 2 nt of the NGG PAM, not the leading N.
    """
    if len(seq23) != 23:
        return None
    s = seq23.upper()
    if any(b not in "ACGT" for b in s):
        return None
    protospacer = s[:20]
    pam2 = s[21:23]
    if pam2 not in {"GG", "AG", "CG", "TG", "GA", "AA", "CA", "TA", "GC", "AC", "CC", "TC", "GT", "AT", "CT", "TT"}:
        return None  # not a 2-nt PAM Doench's table covers
    return protospacer, pam2


def _recall_at_quantile(predicted: Sequence[float], observed: Sequence[float], q: float) -> float:
    """Fraction of the top-`q` observed sites that also fall in the top-`q` predicted sites.

    For q=0.25 with n=100: of the 25 strongest sites by readFraction, how many are in the top 25
    by CFD? Returns 1.0 for a perfect ranker on this `q`. Returns NaN if there aren't enough sites.
    """
    n = len(predicted)
    if n != len(observed):
        raise ValueError("predicted / observed length mismatch")
    k = max(1, int(round(q * n)))
    if k >= n:
        return float("nan")  # the threshold spans everything -- recall is trivially 1.0; not meaningful
    pred_arr = np.asarray(predicted, dtype=float)
    obs_arr = np.asarray(observed, dtype=float)
    # Top-k by each; intersection / k.
    top_pred = set(np.argsort(-pred_arr, kind="mergesort")[:k].tolist())
    top_obs = set(np.argsort(-obs_arr, kind="mergesort")[:k].tolist())
    return len(top_pred & top_obs) / k


def _interpretation(rho: float, recalls: list[RecallAtQuantile], leakage: LeakageAssessment, dataset: str) -> str:
    parts = [
        f"Spearman rho={rho:.3f} between full CFD (Doench 2016) and measured readFraction across "
        f"{dataset} (aggregated validated off-target sites).",
    ]
    if recalls:
        bits = ", ".join(f"top-{int(r.quantile * 100)}%={r.recall:.2f}" for r in recalls)
        parts.append(f"Recall-at-quantile (of strongest sites recovered by top-q CFD): {bits}.")
    if leakage.status == "unknown":
        parts.append(
            "Leakage status is UNKNOWN -- whether CFD's Doench-2016 training screen overlapped "
            "any of these endogenous sites has not been verified against a primary source -- so "
            "this is a discrimination measurement, NOT a held-out accuracy claim."
        )
    elif leakage.status == "held_out":
        parts.append(f"Held-out vs the model's training set. Evidence: {leakage.evidence}")
    elif leakage.status == "contaminated":
        parts.append(f"WARNING: training-set overlap; rho is OPTIMISTIC. Evidence: {leakage.evidence}")
    if leakage.caveat:
        parts.append(f"Residual caveat: {leakage.caveat}")
    return " ".join(parts)


def run_off_target_recall(
    dataset: str = "annotOfftargets",
    *,
    settings: Settings | None = None,
    download_fn: DownloadFn | None = None,
    local_path: str | None = None,
    score_fn: ScoreFn | None = None,
    quantiles: Sequence[float] = (0.10, 0.25, 0.50),
) -> OffTargetRecallResult:
    """Score every (guide, off-target) pair with CFD and measure discrimination vs readFraction.

    The eval data is fetched-on-first-use (or read from `local_path`), sha256-verified. Scoring
    uses the platform's own `cfd_score` (Doench 2016, full CFD = mismatch x PAM) by default;
    tests inject `score_fn` to exercise the math without depending on the real CFD weights.
    """
    s = settings if settings is not None else _default_settings
    loaded = load_dataset(dataset, settings=s, download_fn=download_fn, local_path=local_path)
    if loaded.spec.kind != "off_target":
        raise ValueError(f"Dataset {dataset!r} is kind={loaded.spec.kind!r}; run_off_target_recall needs 'off_target'.")
    rows = [r for r in loaded.rows if isinstance(r, EffOfftargetRow)]  # narrow the union for the type-checker

    score = score_fn if score_fn is not None else cfd_score

    pairs: list[OfftargetPair] = []
    skipped = 0
    for row in rows:
        g = _peel_protospacer_and_pam(row.guide_seq)
        o = _peel_protospacer_and_pam(row.ot_seq)
        if g is None or o is None:
            skipped += 1
            continue
        guide_proto, _ = g
        ot_proto, ot_pam = o
        try:
            cfd = float(score(guide_proto, ot_proto, ot_pam))
        except (ValueError, KeyError):
            skipped += 1
            continue
        pairs.append(
            OfftargetPair(
                guide_name=row.guide_name,
                cfd=cfd,
                read_fraction=row.read_fraction,
                mismatches=row.mismatches,
            )
        )

    if len(pairs) < 2:
        raise ValueError(f"After filtering, only {len(pairs)} scorable pairs remain -- cannot compute recall.")

    preds = [p.cfd for p in pairs]
    obs = [p.read_fraction for p in pairs]
    rho = spearman_rho(preds, obs)
    r = pearson_r(preds, obs)
    recalls = [RecallAtQuantile(quantile=q, recall=round(_recall_at_quantile(preds, obs, q), 4)) for q in quantiles]
    leakage = assess_leakage_offtarget(dataset, "cfd_full")

    return OffTargetRecallResult(
        dataset=dataset,
        model="cfd_full",
        model_version="doench-2016-cfd-full",
        n=len(pairs),
        n_skipped=skipped,
        spearman_rho=round(rho, 4),
        pearson_r=round(r, 4),
        recall_at=recalls,
        leakage_status=leakage.status,
        leakage_evidence=leakage.evidence,
        leakage_caveat=leakage.caveat,
        interpretation=_interpretation(rho, recalls, leakage, dataset),
        source=loaded.source,
        data_sha256=loaded.sha256,
        citation=loaded.spec.citation,
        pairs=pairs,
    )
