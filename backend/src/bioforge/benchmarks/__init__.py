"""BioForge self-measurement benchmarks (v4 §13).

The platform measures its own accuracy against gold-standard truth and publishes the
numbers. It covers ClinVar interpretation fidelity and on-target guide-efficiency
correlation (DeepCRISPR x Chari-2015); GIAB variant calling and GUIDE-seq off-target
recall are follow-ups.
"""

from __future__ import annotations

from bioforge.benchmarks.clinvar_fidelity import (
    FidelityReport,
    FidelityViolation,
    score_clinvar_fidelity,
)
from bioforge.benchmarks.edit_outcome_agreement import (
    EditOutcomeAgreementResult,
    EditOutcomeLabel,
    compare_distributions,
    jensen_shannon_divergence,
    total_variation_distance,
)
from bioforge.benchmarks.effdata import (
    EffDataConsentRequired,
    EffDataFetchError,
    EffDataset,
    EffDataUnknown,
    load_dataset,
)
from bioforge.benchmarks.off_target_recall import (
    OfftargetPair,
    OffTargetRecallResult,
    RecallAtQuantile,
    run_off_target_recall,
)
from bioforge.benchmarks.on_target_efficiency import (
    OnTargetEfficiencyResult,
    pearson_r,
    run_on_target_efficiency,
    spearman_rho,
)
from bioforge.benchmarks.reliability import (
    ReliabilityBin,
    ReliabilityCurve,
    reliability_curve,
    reliability_from_pairs,
)
from bioforge.benchmarks.variant_concordance import (
    ConcordanceMetrics,
    ConfidentRegion,
    VariantCall,
    VariantConcordanceResult,
    score_variant_concordance,
    variant_calls_from_parsed,
)

__all__ = [
    "ConcordanceMetrics",
    "ConfidentRegion",
    "EditOutcomeAgreementResult",
    "EditOutcomeLabel",
    "EffDataConsentRequired",
    "EffDataFetchError",
    "EffDataUnknown",
    "EffDataset",
    "FidelityReport",
    "FidelityViolation",
    "OffTargetRecallResult",
    "OfftargetPair",
    "OnTargetEfficiencyResult",
    "RecallAtQuantile",
    "ReliabilityBin",
    "ReliabilityCurve",
    "VariantCall",
    "VariantConcordanceResult",
    "compare_distributions",
    "jensen_shannon_divergence",
    "load_dataset",
    "pearson_r",
    "reliability_curve",
    "reliability_from_pairs",
    "run_off_target_recall",
    "run_on_target_efficiency",
    "score_clinvar_fidelity",
    "score_variant_concordance",
    "spearman_rho",
    "total_variation_distance",
    "variant_calls_from_parsed",
]
