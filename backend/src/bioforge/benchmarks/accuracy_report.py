"""§13 / §5 — the Accuracy Report: BioForge publishes its own measured accuracy.

This is the in-product surface of the blueprint's defining principle — "a tool that reports
its own error rate is a tool a scientist can defend in review" (§13, non-negotiables 17-18).
It assembles ONLY real, already-computed numbers and an honest ledger of what is not yet
measured. It never invents a benchmark figure (rule 18, §2):

  * **Validator gate (Layer 6, §4).** The grounding validator's measured block-precision /
    fabrication-recall over the committed labeled corpus, plus the release gate those metrics
    must clear. These are computed live from the corpus, not hardcoded.
  * **Model accuracy provenance (§4.2, §6, §14.14).** Each scoring tool's PUBLISHED held-out
    accuracy, pulled verbatim from the registry metadata (each value cites its source or
    carries a `VERIFY:` marker upstream) plus whether it emits instance-level uncertainty.
  * **Benchmark ledger (§13).** Every gold-standard benchmark the blueprint mandates, each
    tagged live / guard-only / not-yet-wired — so the report is honest about its own gaps.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from bioforge import __version__
from bioforge.agent.grounding.metrics import (
    CorpusMetrics,
    evaluate_corpus,
    load_numeric_corpus,
)

# The deterministic grounding layers (numeric L3, identifier L3+) must be perfect — this is
# the release gate enforced by test_grounding_metrics.py. The Accuracy Report surfaces both
# the measured numbers and whether they currently clear the gate.
_GATE_THRESHOLD = 1.0


class ValidatorGate(BaseModel):
    """Layer 6 corpus metrics + the release gate they must clear (§4 L6, rule 17)."""

    metrics: CorpusMetrics
    threshold: float = Field(
        default=_GATE_THRESHOLD,
        description="Required block-precision AND fabrication-recall for each deterministic layer.",
    )
    numeric_passes: bool = Field(description="Numeric layer (L3) meets the gate.")
    entity_passes: bool = Field(description="Identifier layer (L3+) meets the gate.")
    passes: bool = Field(
        description="True iff BOTH deterministic layers clear the gate (a release is blocked otherwise)."
    )


class ModelAccuracyEntry(BaseModel):
    """A registered tool's model provenance + PUBLISHED accuracy (§4.2, §6, §14.14)."""

    tool: str
    model_versions: dict[str, str] = Field(description="Exact model/algorithm version tags this tool reports.")
    published_accuracy: dict[str, str] = Field(
        description="Verbatim from the registry. Each value cites its primary source or carries a VERIFY marker — never an invented figure.",
    )
    emits_instance_uncertainty: dict[str, bool] = Field(
        description="Per output: whether the model emits a calibrated per-prediction interval (drives honest UI; §6).",
    )


class BenchmarkStatus(BaseModel):
    """One §13 gold-standard benchmark and its current wiring status (the honest ledger)."""

    name: str
    blueprint_section: str
    status: Literal["live", "guard_only", "not_yet_wired"] = Field(
        description="live = real metric computed; guard_only = logic unit-tested, no live gold-set; not_yet_wired = mandated but unbuilt.",
    )
    detail: str


class AccuracyReport(BaseModel):
    """The full self-measurement surface served at GET /benchmarks/accuracy."""

    generated_at: datetime
    bioforge_version: str
    validator: ValidatorGate
    models: list[ModelAccuracyEntry]
    benchmarks: list[BenchmarkStatus]


def _validator_gate() -> ValidatorGate:
    metrics = evaluate_corpus(load_numeric_corpus())
    numeric_passes = (
        metrics.numeric_block_precision >= _GATE_THRESHOLD and metrics.numeric_fabrication_recall >= _GATE_THRESHOLD
    )
    entity_passes = (
        metrics.entity_block_precision >= _GATE_THRESHOLD and metrics.entity_fabrication_recall >= _GATE_THRESHOLD
    )
    return ValidatorGate(
        metrics=metrics,
        numeric_passes=numeric_passes,
        entity_passes=entity_passes,
        passes=numeric_passes and entity_passes,
    )


def _model_entries() -> list[ModelAccuracyEntry]:
    """Enumerate registered tools that carry model/accuracy metadata (§4.2), verbatim."""
    import bioforge.tools  # noqa: F401 — ensure every tool module is imported / registered
    from bioforge.tools.registry import list_tools

    entries = [
        ModelAccuracyEntry(
            tool=spec.name,
            model_versions=dict(spec.model_versions),
            published_accuracy=dict(spec.published_accuracy),
            emits_instance_uncertainty=dict(spec.emits_instance_uncertainty),
        )
        for spec in list_tools()
        if spec.published_accuracy or spec.model_versions
    ]
    entries.sort(key=lambda e: e.tool)
    return entries


# The §13 gold-standard ledger. Honest by construction: a benchmark is "live" only when a real
# metric is computed above; the rest are named with their blueprint section and true status so
# the report never overstates what has been measured.
_BENCHMARKS: list[BenchmarkStatus] = [
    BenchmarkStatus(
        name="Grounding validator — numeric (L3)",
        blueprint_section="§4 L6 / §13",
        status="live",
        detail="Block precision + fabrication recall over the committed hand-labeled corpus; release-gated to 1.0.",
    ),
    BenchmarkStatus(
        name="Grounding validator — identifier (L3+)",
        blueprint_section="§4 L6 / §13",
        status="live",
        detail="Block precision + fabrication recall over the committed hand-labeled corpus; release-gated to 1.0.",
    ),
    BenchmarkStatus(
        name="ClinVar interpretation fidelity (>=2 star)",
        blueprint_section="§13",
        status="guard_only",
        detail=(
            "The fidelity guard (verbatim significance, Pathogenic vs Likely-pathogenic kept distinct, star "
            "rating preserved) is unit-tested against faithful + adversarial reference cases, AND a live "
            ">=2-star ClinVar fidelity benchmark is now wired as a nightly online test "
            "(test_clinvar_fidelity_online): gold from an independent NCBI read vs the platform's "
            "lookup_clinvar output, asserting no relabeling. Not computed at request time (no synchronous "
            "NCBI call on page load), so it stays guard_only here."
        ),
    ),
    BenchmarkStatus(
        name="CRISPR on-target — held-out cross-dataset guide-efficiency (Spearman)",
        blueprint_section="§13 / Phase 2",
        status="guard_only",
        detail=(
            "benchmarks.on_target_efficiency correlates DeepCRISPR predictions against measured "
            "Chari-2015 editing efficiency. The fetch-on-first-use loader (sha256-pinned, unlicensed "
            "data never vendored) and the tie-aware numpy Spearman are unit-tested; the real "
            "1234-guide run is a -m docker e2e (live Spearman 0.130, 2026-05-30). Reported guard_only, "
            "not live: it needs a network fetch + an out-of-process DeepCRISPR call, so it is not "
            "computed on page load. Leakage status promoted to HELD-OUT on 2026-06-01 against the "
            "Chuai 2018 primary source (PMC6020378): DeepCRISPR trained on Wang 2014 / Hart 2015 / "
            "Doench 2016 across HCT116/HEK293T/HeLa/HL60; Chari 2015 is reference [12], used only as "
            "independent validation. One residual caveat travels with the result (incidental guide "
            "overlap with the Doench-2016 HEK293T training subset is not sequence-level checked). "
            "Still framed CROSS-DATASET (Haeussler 2016: cross-dataset rho is modest)."
        ),
    ),
    BenchmarkStatus(
        name="CRISPR off-target — validated-site discrimination (CFD vs readFraction)",
        blueprint_section="§13 / Phase 2",
        status="guard_only",
        detail=(
            "benchmarks.off_target_recall scores every (sgRNA, validated off-target) pair from the "
            "aggregated crisporPaper annotOfftargets table (718 sites across Tsai 2015 / Frock 2015 / "
            "Cho 2014 / Kim 2015 / Ran 2015) with the platform's own full CFD (Doench 2016, mismatch x "
            "PAM) and correlates against the upstream readFraction (Spearman + Pearson) plus "
            "recall-at-quantile of the strongest sites. Loader is sha256-pinned + commit-pinned; "
            "fetched-on-first-use, never vendored (same unlicensed-data posture as the on-target arm). "
            "guard_only, not live: it needs a network fetch + per-site CFD, so it is not computed "
            "on page load. Leakage status currently UNKNOWN -- whether CFD's Doench-2016 training "
            "screen overlapped any of these endogenous sites is not yet verified against the primary "
            "source -- so reported as discrimination, NOT a held-out accuracy claim (the same gate "
            "discipline the on-target arm uses)."
        ),
    ),
    BenchmarkStatus(
        name="Variant calling — GIAB precision / recall / F1",
        blueprint_section="§13 / Phase 3",
        status="not_yet_wired",
        detail="No variant-calling path is built yet; the variant tools are annotation/interpretation only.",
    ),
    BenchmarkStatus(
        name="Edit-outcome distribution agreement (TVD + JSD)",
        blueprint_section="§13 / Phase 2",
        status="guard_only",
        detail=(
            "benchmarks.edit_outcome_agreement provides typed Total Variation Distance and "
            "Jensen-Shannon divergence (numpy-only) between a predicted indel distribution and an "
            "observed one, with the same honesty rails as the on/off-target arms (typed "
            "LeakageAssessment; refuses to silently renormalize a malformed distribution; per-label "
            "(predicted, observed) pairs feed the reliability diagram). guard_only because a real "
            "held-out distribution dataset for Lindel / inDelphi / FORECasT is not yet wired -- "
            "those models are validated for PARITY against their own published images, and TVD/JSD "
            "are unit-tested, but the live distribution-vs-distribution comparison needs an "
            "accessible, license-clean held-out source."
        ),
    ),
]


def build_accuracy_report() -> AccuracyReport:
    """Assemble the live Accuracy Report. Pure (no DB); fast deterministic CPU work."""
    return AccuracyReport(
        generated_at=datetime.now(UTC),
        bioforge_version=__version__,
        validator=_validator_gate(),
        models=_model_entries(),
        benchmarks=list(_BENCHMARKS),
    )
