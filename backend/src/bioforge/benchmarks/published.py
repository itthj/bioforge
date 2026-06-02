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
