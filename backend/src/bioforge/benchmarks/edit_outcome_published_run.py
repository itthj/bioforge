"""section-13 edit-outcome benchmark -- the live runner (FORECasT predicted vs observed).

Joins the FORECasT observed indel profiles (figshare, loaded by `forecast_profiles.load_observed`)
to the designed target library (Dataset 1, `forecast_profiles.load_target_library`), runs the
FORECasT predictor over each joined oligo's (target, PAM index), and scores the predicted indel
distribution against the observed one with TVD + JSD (`edit_outcome_agreement.compare_distributions`).
The per-guide scores are aggregated (median + quartiles) into a typed result.

Honesty rails (carried verbatim into the published artifact):
  * Both sides use FORECasT's OWN indel-label taxonomy, so the comparison needs NO remapping.
  * Leakage is 'unknown' (the scorer's gate forbids a 'held_out' claim without a primary source):
    these oligos are FORECasT's own library and K562 is its primary TRAINING cell line, so this is
    an IN-DISTRIBUTION agreement measurement, not a held-out accuracy claim. That caveat travels.
  * Only oligos with >= `min_reads` observed edited reads are scored (a distribution built from a
    handful of reads is noise); the count actually scored (`n_guides`) is reported, never hidden.
  * A predictor error on one oligo increments `n_skipped` (recorded, never silently swallowed); too
    many skips, or too low a join coverage, raises rather than publishing a number built on a
    mismatched / mostly-failed set.

numpy for the aggregation; the FORECasT call is the existing out-of-process runner (injectable
`run_fn` for hermetic tests).
"""

from __future__ import annotations

import numpy as np
from pydantic import BaseModel, Field

from bioforge.benchmarks.edit_outcome_agreement import compare_distributions
from bioforge.benchmarks.forecast_profiles import load_observed, load_target_library
from bioforge.benchmarks.on_target_efficiency import LeakageStatus
from bioforge.config import Settings
from bioforge.config import settings as _default_settings
from bioforge.tools.sequence.models.forecast import (
    ForecastInferenceError,
    ForecastUnavailable,
    predict_forecast,
)
from bioforge.tools.sequence.models.forecast.runner import RunFn

# A measured distribution built from too few edited reads is noise, not ground truth. 100 is a
# defensible floor for a stable per-oligo indel distribution; the real run can raise it.
_DEFAULT_MIN_READS = 100
# If fewer than this fraction of eligible observed oligos join the target library, something is
# wrong with the ID scheme / file -- refuse rather than score a mismatched subset.
_MIN_JOIN_COVERAGE = 0.8
# If more than this fraction of attempted oligos fail in the predictor, refuse the run.
_MAX_SKIP_FRACTION = 0.2

_IN_DISTRIBUTION_CAVEAT = (
    "K562 is FORECasT's primary TRAINING cell line and these oligos are FORECasT's own library; the "
    "train/test split for these specific guides is not verified against a primary source. So this is "
    "an IN-DISTRIBUTION distribution-agreement measurement, NOT a held-out accuracy claim."
)


class PerGuideAgreement(BaseModel):
    """One oligo's predicted-vs-observed agreement + the observed read depth behind it."""

    oligo_id: str
    tvd: float
    jsd: float
    observed_reads: int
    n_labels: int


class EditOutcomePublishedResult(BaseModel):
    """Aggregated FORECasT-vs-observed distribution agreement over a sample, plus honesty labels."""

    observed_sample: str
    sample_label: str
    model: str = "forecast"
    model_version: str
    target_library: str
    min_reads: int
    direction: str = Field(
        description="Design strand scored (FORWARD); REVERSE records are excluded, see interpretation."
    )
    n_eligible: int = Field(description="Observed oligos with >= min_reads edited reads (candidates).")
    n_joined: int = Field(description="Eligible oligos also present in the target library (ID-join health).")
    n_guides: int = Field(description="Oligos actually scored (after the max_guides cap).")
    n_skipped: int = Field(description="Joined oligos the predictor could not score (recorded, not hidden).")
    join_coverage: float
    tvd_median: float
    tvd_q1: float
    tvd_q3: float
    jsd_median: float
    jsd_q1: float
    jsd_q3: float
    leakage_status: LeakageStatus
    leakage_evidence: str = ""
    leakage_caveat: str
    observed_sha256: str
    target_sha256: str
    citations: list[str]
    interpretation: str
    per_guide: list[PerGuideAgreement]


def _quartiles(values: list[float]) -> tuple[float, float, float]:
    arr = np.asarray(values, dtype=float)
    return (
        float(np.median(arr)),
        float(np.percentile(arr, 25)),
        float(np.percentile(arr, 75)),
    )


def run_edit_outcome_agreement(
    observed_name: str,
    target_name: str,
    *,
    settings: Settings | None = None,
    max_guides: int | None = None,
    min_reads: int = _DEFAULT_MIN_READS,
    direction: str = "FORWARD",
    observed_local_path: str | None = None,
    target_local_path: str | None = None,
    run_fn: RunFn | None = None,
) -> EditOutcomePublishedResult:
    """Run the FORECasT-predicted vs observed edit-outcome agreement benchmark.

    Deterministic: oligos are scored in sorted id order, so `max_guides` selects a stable subset and
    the result reproduces. Only `direction`-strand design records are scored (FORWARD by default):
    the predictor's indelgentarget rejects the REVERSE design records as-provided, and strand is a
    design artifact orthogonal to model accuracy, so we score one strand and document the restriction
    rather than guess a reverse-complement frame. Raises if the observed<->target join coverage is too
    low or too many oligos fail in the predictor (never publish a number built on a mismatched /
    mostly-failed set).
    """
    s = settings if settings is not None else _default_settings

    observed = load_observed(observed_name, settings=s, local_path=observed_local_path)
    library = load_target_library(target_name, settings=s, local_path=target_local_path)

    eligible = {
        oid: prof for oid, prof in observed.profiles.items() if prof.total_reads >= min_reads and prof.distribution
    }
    in_library = sorted(oid for oid in eligible if oid in library.records)
    if not in_library:
        raise ValueError(
            f"No eligible observed oligos (>= {min_reads} reads) join the target library "
            f"{target_name!r}. Check the ID schemes match (observed @@@Oligo<N> vs library ids)."
        )
    coverage = len(in_library) / len(eligible)
    if coverage < _MIN_JOIN_COVERAGE:
        raise ValueError(
            f"Join coverage {coverage:.2f} below {_MIN_JOIN_COVERAGE}: only {len(in_library)} of "
            f"{len(eligible)} eligible observed oligos are in the target library. Refusing to score a "
            "mismatched subset -- re-verify the observed/library ID schemes."
        )
    candidates = [oid for oid in in_library if library.records[oid].direction == direction]
    if not candidates:
        raise ValueError(f"No {direction}-strand design records among the {len(in_library)} joined oligos.")

    selected = candidates if max_guides is None else candidates[:max_guides]
    model_version = "allen-2018"

    per_guide: list[PerGuideAgreement] = []
    n_skipped = 0
    for oid in selected:
        rec = library.records[oid]
        try:
            pred = predict_forecast(rec.target, rec.pam_index, settings=s, run_fn=run_fn)
        except ForecastInferenceError:
            n_skipped += 1
            continue
        if not pred.predictions:
            n_skipped += 1
            continue
        agreement = compare_distributions(
            pred.predictions,
            eligible[oid].distribution,
            dataset=observed_name,
            model="forecast",
            model_version=model_version,
        )
        per_guide.append(
            PerGuideAgreement(
                oligo_id=oid,
                tvd=agreement.tvd,
                jsd=agreement.jsd,
                observed_reads=eligible[oid].total_reads,
                n_labels=agreement.n_labels,
            )
        )

    if not per_guide:
        raise ForecastUnavailable(
            "Scored zero oligos -- every FORECasT prediction failed. Verify the FORECasT image is "
            "enabled and reachable (BIOFORGE_FORECAST_ENABLED + image)."
        )
    skip_fraction = n_skipped / len(selected)
    if skip_fraction > _MAX_SKIP_FRACTION:
        raise ForecastInferenceError(
            f"{n_skipped} of {len(selected)} oligos failed in the predictor ({skip_fraction:.0%} > "
            f"{_MAX_SKIP_FRACTION:.0%}). Refusing to publish a number built on a mostly-failed set."
        )

    tvd_median, tvd_q1, tvd_q3 = _quartiles([g.tvd for g in per_guide])
    jsd_median, jsd_q1, jsd_q3 = _quartiles([g.jsd for g in per_guide])
    interpretation = (
        f"FORECasT (Allen 2018) predicted indel distributions vs the measured profiles on "
        f"{observed.spec.sample_label}: median TVD {tvd_median:.3f} (IQR {tvd_q1:.3f}-{tvd_q3:.3f}), "
        f"median JSD {jsd_median:.3f}, over n={len(per_guide)} {direction}-strand guides (>= {min_reads} "
        f"edited reads each). TVD is the fraction of outcome mass misallocated on average; lower is "
        f"better. Scored {direction}-strand design records only (the predictor's indelgentarget rejects "
        f"the REVERSE records as-provided; strand is orthogonal to model accuracy). "
        f"Leakage UNKNOWN: {_IN_DISTRIBUTION_CAVEAT}"
    )

    return EditOutcomePublishedResult(
        observed_sample=observed_name,
        sample_label=observed.spec.sample_label,
        model="forecast",
        model_version=model_version,
        target_library=target_name,
        min_reads=min_reads,
        direction=direction,
        n_eligible=len(eligible),
        n_joined=len(in_library),
        n_guides=len(per_guide),
        n_skipped=n_skipped,
        join_coverage=round(coverage, 4),
        tvd_median=round(tvd_median, 4),
        tvd_q1=round(tvd_q1, 4),
        tvd_q3=round(tvd_q3, 4),
        jsd_median=round(jsd_median, 4),
        jsd_q1=round(jsd_q1, 4),
        jsd_q3=round(jsd_q3, 4),
        leakage_status="unknown",
        leakage_evidence="",
        leakage_caveat=_IN_DISTRIBUTION_CAVEAT,
        observed_sha256=observed.sha256,
        target_sha256=library.sha256,
        citations=[observed.spec.citation, library.spec.citation],
        interpretation=interpretation,
        per_guide=per_guide,
    )
