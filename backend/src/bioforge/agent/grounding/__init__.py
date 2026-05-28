"""Grounding validator (BioForge v4 §4) — defense-in-depth against hallucination.

Slice 1 ships Layer 3: deterministic numeric grounding. The claim classifier (L2),
entity/mechanistic judge (L4), rewrite re-validation (L5), and validate-the-validator
corpus (L6) land in subsequent slices and will extend the same `ValidationReport`.
"""

from __future__ import annotations

from bioforge.agent.grounding.judge import (
    JudgeResult,
    judge_claims,
)
from bioforge.agent.grounding.metrics import (
    CorpusMetrics,
    evaluate_numeric_corpus,
    load_numeric_corpus,
)
from bioforge.agent.grounding.numeric import (
    InventoryEntry,
    ParsedNumber,
    build_inventory,
    extract_numeric_claims,
    ground_response,
)
from bioforge.agent.grounding.report import (
    ClaimKind,
    GroundingStatus,
    JudgedClaim,
    NumericClaimVerdict,
    ValidationReport,
)
from bioforge.agent.grounding.soundness import (
    SoundnessReport,
    SoundnessViolation,
    check_soundness,
)

__all__ = [
    "ClaimKind",
    "CorpusMetrics",
    "GroundingStatus",
    "InventoryEntry",
    "JudgeResult",
    "JudgedClaim",
    "NumericClaimVerdict",
    "ParsedNumber",
    "SoundnessReport",
    "SoundnessViolation",
    "ValidationReport",
    "build_inventory",
    "check_soundness",
    "evaluate_numeric_corpus",
    "extract_numeric_claims",
    "ground_response",
    "judge_claims",
    "load_numeric_corpus",
]
