"""§13 edit-outcome distribution-agreement benchmark.

For an edit-outcome predictor (Lindel / inDelphi / FORECasT) on a given guide, compare the
PREDICTED indel-frequency distribution to the OBSERVED one with a distance metric. Two metrics
are reported because they say different things:

  * **Total Variation Distance (TVD)** -- the maximum probability difference between the two
    distributions over any event. TVD in [0, 1]; 0 = identical, 1 = disjoint support. Easy to
    explain ("on average, X% of outcomes are misallocated"), and the canonical interpretable
    score for indel-distribution agreement.
  * **Jensen-Shannon divergence (JSD)** -- the symmetric, smoothed KL divergence; JSD in [0, 1]
    (base 2). Penalises sharp disagreement more than TVD does and stays well-defined when one
    distribution puts zero mass on a label the other supports.

The two distributions are dicts keyed by indel-LABEL strings (Lindel '-2+4' / inDelphi
'2bp_del' / FORECasT 'I1_L-3C2R0' / etc.) -- the keys are passed through verbatim and the
union of keys defines the event space. Probabilities are NOT silently rescaled: an input that
doesn't sum to 1.0 within tolerance raises, because a wrong-normalization comparison would be
a confident-wrong-number (the rule the platform exists to refuse).

Honesty rails (same design as on/off-target):
  * Typed `LeakageAssessment` for (dataset, model). 'unknown' until verified against the
    model's primary source; 'held_out' / 'contaminated' MUST carry primary-source evidence.
  * `pairs` is the per-label (predicted, observed) probability pairs -- so the same reliability
    diagram component the on-target arm uses can visualise per-label agreement too.

numpy only.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
from pydantic import BaseModel, Field

from bioforge.benchmarks.on_target_efficiency import LeakageAssessment, LeakageStatus

# Sum-to-one tolerance: a distribution that drifts beyond this is rejected, not auto-renormalized.
_NORMALIZATION_TOL = 1e-6


def _validate_distribution(d: Mapping[str, float], name: str) -> dict[str, float]:
    """Refuse anything that isn't a non-empty, non-negative, sum-to-1 (within tol) distribution."""
    if not d:
        raise ValueError(f"{name} distribution is empty.")
    out: dict[str, float] = {}
    total = 0.0
    for label, prob in d.items():
        p = float(prob)
        if p < 0 or not np.isfinite(p):
            raise ValueError(f"{name} distribution has invalid probability for label {label!r}: {p!r}")
        out[str(label)] = p
        total += p
    if abs(total - 1.0) > _NORMALIZATION_TOL:
        raise ValueError(
            f"{name} distribution sums to {total!r}, not 1 (tolerance {_NORMALIZATION_TOL}). Refusing "
            "to silently renormalize -- a wrong-normalization comparison would be a confident-wrong-number."
        )
    return out


def _align(p: Mapping[str, float], q: Mapping[str, float]) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Sorted union of labels + aligned probability vectors (0 where one side is missing)."""
    labels = sorted(set(p) | set(q))
    pa = np.array([p.get(k, 0.0) for k in labels], dtype=float)
    qa = np.array([q.get(k, 0.0) for k in labels], dtype=float)
    return pa, qa, labels


def total_variation_distance(p: Mapping[str, float], q: Mapping[str, float]) -> float:
    """TVD(p, q) = 1/2 sum_i |p_i - q_i|. In [0, 1]; 0 = identical, 1 = disjoint."""
    pa = _validate_distribution(p, "p")
    qa = _validate_distribution(q, "q")
    pv, qv, _ = _align(pa, qa)
    return float(0.5 * np.sum(np.abs(pv - qv)))


def jensen_shannon_divergence(p: Mapping[str, float], q: Mapping[str, float]) -> float:
    """JSD(p, q) in [0, 1] (log base 2). Symmetric, smooth, defined for disjoint support.

    JSD = 0.5 * KL(p || m) + 0.5 * KL(q || m), where m = 0.5 * (p + q). Convention 0*log(0) = 0.
    """
    pa = _validate_distribution(p, "p")
    qa = _validate_distribution(q, "q")
    pv, qv, _ = _align(pa, qa)
    m = 0.5 * (pv + qv)

    def _kl_div(a: np.ndarray, b: np.ndarray) -> float:
        # 0 * log(0/b) = 0 by convention; skip those terms with `where`.
        with np.errstate(divide="ignore", invalid="ignore"):
            term = np.where(a > 0, a * (np.log2(a) - np.log2(b)), 0.0)
        return float(np.sum(term))

    return float(0.5 * _kl_div(pv, m) + 0.5 * _kl_div(qv, m))


class EditOutcomeLabel(BaseModel):
    """One indel label and its predicted vs observed probability pair (reliability inputs)."""

    label: str
    predicted: float
    observed: float


class EditOutcomeAgreementResult(BaseModel):
    """Agreement of a predicted indel distribution with an observed one, plus honesty labels."""

    dataset: str
    model: str
    model_version: str
    n_labels: int = Field(description="Size of the union of indel labels across the two distributions.")
    tvd: float = Field(description="Total variation distance in [0, 1]; 0 = identical, 1 = disjoint.")
    jsd: float = Field(description="Jensen-Shannon divergence (log base 2) in [0, 1]; symmetric, smooth.")
    leakage_status: LeakageStatus
    leakage_evidence: str = ""
    leakage_caveat: str = ""
    interpretation: str
    pairs: list[EditOutcomeLabel] = Field(
        description="Per-label (predicted, observed) probabilities -- inputs to the reliability diagram.",
    )


# (dataset, model) -> typed LeakageAssessment. 'unknown' by default; no entry is ever fabricated.
# Promote with primary-source evidence (the same gate the on-target arm uses) when a real
# held-out dataset for a model is identified (e.g. Lindel's own test set, FORECasT's test set).
_LEAKAGE: dict[tuple[str, str], LeakageAssessment] = {}


def assess_leakage_edit_outcome(dataset: str, model: str) -> LeakageAssessment:
    """Typed leakage assessment for (dataset, model), defaulting to ('unknown', '')."""
    return _LEAKAGE.get((dataset, model), LeakageAssessment(status="unknown", evidence=""))


def _interpretation(tvd: float, jsd: float, leakage: LeakageAssessment, dataset: str, model: str) -> str:
    parts = [
        f"TVD={tvd:.3f}, JSD={jsd:.3f} between the {model} prediction and the observed indel "
        f"distribution on {dataset}.",
        "TVD is the fraction of outcome mass that is misallocated on average (0=identical, 1=disjoint); "
        "JSD penalises sharp disagreement more.",
    ]
    if leakage.status == "unknown":
        parts.append(
            "Leakage status is UNKNOWN -- whether this comparison set was in the model's training "
            "data has not been verified against a primary source -- so this is an agreement "
            "measurement, NOT a held-out accuracy claim."
        )
    elif leakage.status == "held_out":
        parts.append(f"Held-out vs the model's training set. Evidence: {leakage.evidence}")
    elif leakage.status == "contaminated":
        parts.append(f"WARNING: training-set overlap; tvd/jsd are OPTIMISTIC. Evidence: {leakage.evidence}")
    if leakage.caveat:
        parts.append(f"Residual caveat: {leakage.caveat}")
    return " ".join(parts)


def compare_distributions(
    predicted: Mapping[str, float],
    observed: Mapping[str, float],
    *,
    dataset: str,
    model: str,
    model_version: str,
) -> EditOutcomeAgreementResult:
    """Compare predicted and observed indel distributions; return the typed agreement result.

    Both inputs must be non-empty, non-negative, sum-to-1 (within tolerance) distributions over
    indel-label strings. The union of labels defines the event space. The model_version is
    recorded verbatim for provenance (e.g. 'lindel-fdcad58', 'forecast-2018').
    """
    p = _validate_distribution(predicted, "predicted")
    o = _validate_distribution(observed, "observed")
    pv, ov, labels = _align(p, o)

    tvd = total_variation_distance(p, o)
    jsd = jensen_shannon_divergence(p, o)
    leakage = assess_leakage_edit_outcome(dataset, model)

    return EditOutcomeAgreementResult(
        dataset=dataset,
        model=model,
        model_version=model_version,
        n_labels=len(labels),
        tvd=round(tvd, 4),
        jsd=round(jsd, 4),
        leakage_status=leakage.status,
        leakage_evidence=leakage.evidence,
        leakage_caveat=leakage.caveat,
        interpretation=_interpretation(tvd, jsd, leakage, dataset, model),
        pairs=[
            EditOutcomeLabel(label=lbl, predicted=float(pp), observed=float(oo))
            for lbl, pp, oo in zip(labels, pv, ov, strict=True)
        ],
    )
