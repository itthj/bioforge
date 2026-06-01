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
from bioforge.benchmarks.effdata import (
    EffDataConsentRequired,
    EffDataFetchError,
    EffDataset,
    EffDataUnknown,
    load_dataset,
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

__all__ = [
    "EffDataConsentRequired",
    "EffDataFetchError",
    "EffDataUnknown",
    "EffDataset",
    "FidelityReport",
    "FidelityViolation",
    "OnTargetEfficiencyResult",
    "ReliabilityBin",
    "ReliabilityCurve",
    "load_dataset",
    "pearson_r",
    "reliability_curve",
    "reliability_from_pairs",
    "run_on_target_efficiency",
    "score_clinvar_fidelity",
    "spearman_rho",
]
