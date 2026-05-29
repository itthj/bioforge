"""BioForge self-measurement benchmarks (v4 §13).

The platform measures its own accuracy against gold-standard truth and publishes the
numbers. This package starts with ClinVar interpretation fidelity; biology gold-sets
(GIAB variant calling, GUIDE-seq off-target, held-out guide efficiency) are follow-ups.
"""

from __future__ import annotations

from bioforge.benchmarks.clinvar_fidelity import (
    FidelityReport,
    FidelityViolation,
    score_clinvar_fidelity,
)

__all__ = ["FidelityReport", "FidelityViolation", "score_clinvar_fidelity"]
