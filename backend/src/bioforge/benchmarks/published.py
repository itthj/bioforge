"""Published benchmark results -- real measurements the platform serves in its Accuracy Report.

The §13 benchmarks need a network fetch + an out-of-process model call, so they cannot run
synchronously on a page load. Instead, an OFFLINE generation step runs a benchmark for real and
writes a provenance-stamped JSON artifact here; `build_accuracy_report()` loads those artifacts and
serves them. This is how the platform "publishes its own accuracy" (§13) without a 7.8 GB Docker
run per request.

Honesty: each artifact carries `generated_at` (when the benchmark actually ran), the model version,
the eval-data sha256, and the same leakage labels the live result emits -- so the UI shows a real,
reproducible snapshot, clearly dated, never a number computed on the fly or invented. Re-running the
generator against the pinned commit + pinned image reproduces the artifact.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from bioforge.benchmarks.reliability import ReliabilityCurve, reliability_curve, reliability_from_pairs
from bioforge.benchmarks.variant_concordance import ConcordanceMetrics

PUBLISHED_DIR = Path(__file__).parent / "published"


class PublishedBenchmark(BaseModel):
    """One real, dated benchmark measurement plus the reliability curve behind it."""

    name: str
    blueprint_section: str
    generated_at: datetime = Field(description="When the benchmark actually ran (NOT the report build time).")
    model_version: str
    dataset: str
    data_sha256: str
    citation: str
    n: int
    spearman_rho: float
    pearson_r: float
    leakage_status: str
    leakage_evidence: str = ""
    leakage_caveat: str = ""
    dataset_relationship: str = ""
    interpretation: str
    reliability: ReliabilityCurve


def load_published_benchmarks() -> list[PublishedBenchmark]:
    """Load every committed benchmark artifact from `published/`. Returns [] if none exist.

    Tolerant by design: a single malformed artifact is skipped (logged-by-omission) rather than
    breaking the whole Accuracy Report -- the report must always render the parts that ARE valid.
    """
    if not PUBLISHED_DIR.exists():
        return []
    out: list[PublishedBenchmark] = []
    for path in sorted(PUBLISHED_DIR.glob("*.json")):
        if path.name.startswith(("giab_", "edit_outcome_")):
            continue  # different-shaped artifacts -> load_published_giab() / load_published_edit_outcome()
        try:
            out.append(PublishedBenchmark.model_validate_json(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


# --- offline generation (run via `python -m bioforge.benchmarks.published`) ----------------------


def generate_on_target_artifact(*, settings=None) -> Path:
    """Run the real DeepCRISPR x Chari-2015 on-target benchmark and write its artifact.

    Requires the opt-in legacy env (BIOFORGE_DEEPCRISPR_ENABLED + image) and effData consent. This
    is the ONLY place a 1234-guide Docker run happens -- intentionally offline, not on a request.
    """
    from bioforge.benchmarks.on_target_efficiency import run_on_target_efficiency

    result = run_on_target_efficiency("chari2015Train", model="deepcrispr", settings=settings)
    curve = reliability_from_pairs(
        result.pairs,
        n_bins=10,
        predicted_label="DeepCRISPR on-target score",
        observed_label="Chari-2015 measured efficiency",
    )
    artifact = PublishedBenchmark(
        name="CRISPR on-target: DeepCRISPR vs Chari-2015 (held-out, cross-dataset)",
        blueprint_section="§13 / Phase 2",
        generated_at=datetime.now(UTC),
        model_version=result.model_version,
        dataset=result.dataset,
        data_sha256=result.data_sha256,
        citation=result.citation,
        n=result.n,
        spearman_rho=result.spearman_rho,
        pearson_r=result.pearson_r,
        leakage_status=result.leakage_status,
        leakage_evidence=result.leakage_evidence,
        leakage_caveat=result.leakage_caveat,
        dataset_relationship=result.dataset_relationship,
        interpretation=result.interpretation,
        reliability=curve,
    )
    PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PUBLISHED_DIR / "on_target_chari2015_deepcrispr.json"
    out_path.write_text(artifact.model_dump_json(indent=2), encoding="utf-8")
    return out_path


def generate_off_target_artifact(*, settings=None) -> Path:
    """Run the real CFD vs annotOfftargets discrimination benchmark and write its artifact.

    CFD is in-platform (the committed Doench-2016 tables), so this needs only effData consent + a
    network fetch -- no Docker. Honest: leakage stays 'unknown' (CFD's training-overlap with these
    endogenous sites is unverified) with its caveat, exactly as the live result reports.
    """
    from bioforge.benchmarks.off_target_recall import run_off_target_recall

    result = run_off_target_recall("annotOfftargets", settings=settings)
    curve = reliability_curve(
        [(p.cfd, p.read_fraction) for p in result.pairs],
        n_bins=10,
        predicted_label="CFD off-target score",
        observed_label="validated-site readFraction",
    )
    artifact = PublishedBenchmark(
        name="CRISPR off-target: CFD vs validated-site readFraction (Tsai/Frock/Cho/Kim)",
        blueprint_section="§13 / Phase 2",
        generated_at=datetime.now(UTC),
        model_version=result.model_version,
        dataset=result.dataset,
        data_sha256=result.data_sha256,
        citation=result.citation,
        n=result.n,
        spearman_rho=result.spearman_rho,
        pearson_r=result.pearson_r,
        leakage_status=result.leakage_status,
        leakage_evidence=result.leakage_evidence,
        leakage_caveat=result.leakage_caveat,
        dataset_relationship="",
        interpretation=result.interpretation,
        reliability=curve,
    )
    PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PUBLISHED_DIR / "off_target_annotofftargets_cfd.json"
    out_path.write_text(artifact.model_dump_json(indent=2), encoding="utf-8")
    return out_path


# --- GIAB concordance (precision/recall/F1, not a correlation -> its own artifact shape) -------


class PublishedGiabBenchmark(BaseModel):
    """A real, dated GIAB-style variant-calling concordance measurement.

    Distinct from PublishedBenchmark (which is correlation + reliability-curve shaped): variant
    calling is scored as stratified precision/recall/F1, so it carries `by_class` metrics instead.
    """

    name: str
    blueprint_section: str
    generated_at: datetime = Field(description="When the benchmark actually ran.")
    caller: str = Field(description="Caller + digest-pinned image, e.g. 'DeepVariant google/deepvariant@sha256:...'.")
    reference_build: str = Field(description="The USER-CONFIRMED reference build the call was made against.")
    regions: str
    sample: str = Field(description="The sample evaluated, e.g. 'NA12878 (HG001)'.")
    truth_set: str = Field(description="The truth set + its provenance.")
    n_truth_in_regions: int
    n_called_in_regions: int
    by_class: list[ConcordanceMetrics]
    caveat: str
    interpretation: str


def load_published_giab() -> list[PublishedGiabBenchmark]:
    """Load committed GIAB concordance artifacts (`published/giab_*.json`). Tolerant: skips a
    malformed file rather than breaking the report."""
    if not PUBLISHED_DIR.exists():
        return []
    out: list[PublishedGiabBenchmark] = []
    for path in sorted(PUBLISHED_DIR.glob("giab_*.json")):
        try:
            out.append(PublishedGiabBenchmark.model_validate_json(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def generate_giab_artifact(
    *,
    settings=None,
    sample: str,
    truth_set: str,
    interpretation: str,
    name: str,
    slug: str,
    result=None,
) -> Path:
    """Run the real GIAB concordance benchmark (or accept a precomputed result) and write its
    artifact to `published/giab_<slug>.json`. Requires DeepVariant + staged GIAB inputs."""
    from bioforge.benchmarks.giab import run_giab_benchmark

    res = result if result is not None else run_giab_benchmark(settings=settings)
    artifact = PublishedGiabBenchmark(
        name=name,
        blueprint_section="§13 / Phase 3",
        generated_at=datetime.now(UTC),
        caller=res.caller,
        reference_build=res.reference_build,
        regions=res.regions,
        sample=sample,
        truth_set=truth_set,
        n_truth_in_regions=res.concordance.n_truth_in_regions,
        n_called_in_regions=res.concordance.n_called_in_regions,
        by_class=res.concordance.by_class,
        caveat=res.concordance.caveat,
        interpretation=interpretation,
    )
    PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PUBLISHED_DIR / f"giab_{slug}.json"
    out_path.write_text(artifact.model_dump_json(indent=2), encoding="utf-8")
    return out_path


# --- edit-outcome distribution agreement (TVD/JSD, not a correlation -> its own artifact shape) ---


class TvdHistogramBin(BaseModel):
    """One bin of the per-guide TVD distribution (for a no-dependency frontend histogram)."""

    lo: float
    hi: float
    count: int


class PublishedEditOutcomeBenchmark(BaseModel):
    """A real, dated FORECasT-vs-observed indel distribution-agreement measurement (TVD + JSD)."""

    name: str
    blueprint_section: str
    generated_at: datetime = Field(description="When the benchmark actually ran.")
    model: str
    model_version: str
    predictor_image: str = Field(description="Digest-pinned FORECasT image the predictions were run with.")
    observed_dataset: str
    observed_sha256: str
    target_library: str
    target_sha256: str
    sample: str
    direction: str
    min_reads: int
    n_guides: int
    n_skipped: int
    tvd_median: float
    tvd_q1: float
    tvd_q3: float
    jsd_median: float
    jsd_q1: float
    jsd_q3: float
    tvd_histogram: list[TvdHistogramBin]
    leakage_status: str
    leakage_evidence: str = ""
    leakage_caveat: str
    citation: str
    interpretation: str


def load_published_edit_outcome() -> list[PublishedEditOutcomeBenchmark]:
    """Load committed edit-outcome artifacts (`published/edit_outcome_*.json`). Tolerant: skips a
    malformed file rather than breaking the report."""
    if not PUBLISHED_DIR.exists():
        return []
    out: list[PublishedEditOutcomeBenchmark] = []
    for path in sorted(PUBLISHED_DIR.glob("edit_outcome_*.json")):
        try:
            out.append(PublishedEditOutcomeBenchmark.model_validate_json(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def _tvd_histogram(values: list[float], *, n_bins: int = 10) -> list[TvdHistogramBin]:
    """Bin per-guide TVDs into [0, 1] deciles (TVD is bounded in [0, 1])."""
    edges = [i / n_bins for i in range(n_bins + 1)]
    bins = [TvdHistogramBin(lo=edges[i], hi=edges[i + 1], count=0) for i in range(n_bins)]
    for v in values:
        idx = min(int(v * n_bins), n_bins - 1)
        bins[idx] = bins[idx].model_copy(update={"count": bins[idx].count + 1})
    return bins


def generate_edit_outcome_artifact(
    *,
    settings=None,
    observed_name: str = "K562_LV7A_DPI7",
    target_name: str = "self_target_oligos",
    max_guides: int | None = None,
    min_reads: int = 100,
    observed_local_path: str | None = None,
    target_local_path: str | None = None,
    result=None,
) -> Path:
    """Run the real FORECasT-vs-observed edit-outcome benchmark (or accept a precomputed result) and
    write its artifact to `published/edit_outcome_<observed>.json`. Requires the FORECasT image +
    the staged observed sample + the target library (Dataset 1, via local_path)."""
    from bioforge.benchmarks.edit_outcome_published_run import run_edit_outcome_agreement

    res = result or run_edit_outcome_agreement(
        observed_name,
        target_name,
        settings=settings,
        max_guides=max_guides,
        min_reads=min_reads,
        observed_local_path=observed_local_path,
        target_local_path=target_local_path,
    )
    image = getattr(settings, "forecast_docker_image", "") if settings is not None else ""
    artifact = PublishedEditOutcomeBenchmark(
        name="CRISPR edit outcome: FORECasT predicted vs measured indel profiles (TVD/JSD)",
        blueprint_section="section 13 / Phase 2",
        generated_at=datetime.now(UTC),
        model=res.model,
        model_version=res.model_version,
        predictor_image=image,
        observed_dataset=res.observed_sample,
        observed_sha256=res.observed_sha256,
        target_library=res.target_library,
        target_sha256=res.target_sha256,
        sample=res.sample_label,
        direction=res.direction,
        min_reads=res.min_reads,
        n_guides=res.n_guides,
        n_skipped=res.n_skipped,
        tvd_median=res.tvd_median,
        tvd_q1=res.tvd_q1,
        tvd_q3=res.tvd_q3,
        jsd_median=res.jsd_median,
        jsd_q1=res.jsd_q1,
        jsd_q3=res.jsd_q3,
        tvd_histogram=_tvd_histogram([g.tvd for g in res.per_guide]),
        leakage_status=res.leakage_status,
        leakage_evidence=res.leakage_evidence,
        leakage_caveat=res.leakage_caveat,
        citation="; ".join(res.citations),
        interpretation=res.interpretation,
    )
    PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PUBLISHED_DIR / f"edit_outcome_{observed_name.lower()}.json"
    out_path.write_text(artifact.model_dump_json(indent=2), encoding="utf-8")
    return out_path


if __name__ == "__main__":  # pragma: no cover -- offline generation entry point
    # Regenerates ALL published artifacts. On-target needs the DeepCRISPR legacy image; off-target
    # needs only the effData consent + network (in-platform CFD).
    os.environ.setdefault("BIOFORGE_DEEPCRISPR_ENABLED", "true")
    os.environ.setdefault("BIOFORGE_DEEPCRISPR_RUNNER", "docker")
    os.environ.setdefault("BIOFORGE_DEEPCRISPR_DOCKER_IMAGE", "bioforge/deepcrispr:legacy")
    os.environ.setdefault("BIOFORGE_CRISPOR_EFFDATA_CONSENT", "true")
    os.environ.setdefault("BIOFORGE_DEEPCRISPR_TIMEOUT_SECONDS", "900")
    # Re-read settings AFTER setting env (the module-level singleton may have been built already).
    from bioforge.config import Settings

    s = Settings()
    print(f"wrote {generate_on_target_artifact(settings=s)}")
    print(f"wrote {generate_off_target_artifact(settings=s)}")
