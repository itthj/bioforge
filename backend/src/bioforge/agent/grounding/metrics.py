"""Layer 6 — validate the validator (BioForge v4 §4, §13).

The grounding validator is software that can be wrong, so we measure it like any other
accuracy-critical component: a hand-labeled corpus of cases, scored for **block precision**
and **fabrication recall**, separately for each deterministic layer:

  - numeric grounding (L3),
  - structured-identifier grounding (L3+).

block precision — of the values/ids the validator flagged, how many were truly
unsupported (a false positive wrongly redacts real science). fabrication recall — of the
truly-unsupported ones, how many were caught (a false negative is a fabrication that
slipped through). Comparing predicted-vs-expected by value/id means an extraction miss
counts as a false negative — so the whole pipeline is measured, not just the matcher.

These are first-class, release-gating metrics (see test_grounding_metrics.py) and are what
the platform's Accuracy Report surfaces for the deterministic grounding layers.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from bioforge.agent.grounding.numeric import ground_response

_CORPUS_DIR = Path(__file__).parent / "corpus"


class CorpusMetrics(BaseModel):
    """Precision/recall of the deterministic grounding layers over a labeled corpus."""

    n_cases: int
    # Numeric layer (L3)
    numeric_true_positives: int
    numeric_false_positives: int = Field(description="Grounded values wrongly flagged (over-blocks).")
    numeric_false_negatives: int
    numeric_block_precision: float = Field(description="1.0 = never over-blocks a real value.")
    numeric_fabrication_recall: float = Field(description="1.0 = catches every labeled fabricated value.")
    # Structured-identifier layer (L3+)
    entity_true_positives: int
    entity_false_positives: int = Field(description="Grounded identifiers wrongly flagged (over-blocks).")
    entity_false_negatives: int
    entity_block_precision: float = Field(description="1.0 = never over-blocks a real identifier.")
    entity_fabrication_recall: float = Field(description="1.0 = catches every labeled fabricated identifier.")


def _precision(tp: int, fp: int) -> float:
    return tp / (tp + fp) if (tp + fp) else 1.0


def _recall(tp: int, fn: int) -> float:
    return tp / (tp + fn) if (tp + fn) else 1.0


def evaluate_corpus(cases: list[dict]) -> CorpusMetrics:
    """Score the deterministic numeric + identifier validators against a labeled corpus."""
    n_tp = n_fp = n_fn = 0
    e_tp = e_fp = e_fn = 0
    for case in cases:
        report = ground_response(
            case["response"],
            case["tool_outputs"],
            extra_sources=case.get("extra_sources", []),
        )
        num_pred = {round(v.value, 9) for v in report.unsupported}
        num_exp = {round(float(x), 9) for x in case.get("expected_unsupported", [])}
        n_tp += len(num_pred & num_exp)
        n_fp += len(num_pred - num_exp)
        n_fn += len(num_exp - num_pred)

        id_pred = {v.text.upper() for v in report.unsupported_entities}
        id_exp = {str(x).upper() for x in case.get("expected_unsupported_ids", [])}
        e_tp += len(id_pred & id_exp)
        e_fp += len(id_pred - id_exp)
        e_fn += len(id_exp - id_pred)

    return CorpusMetrics(
        n_cases=len(cases),
        numeric_true_positives=n_tp,
        numeric_false_positives=n_fp,
        numeric_false_negatives=n_fn,
        numeric_block_precision=_precision(n_tp, n_fp),
        numeric_fabrication_recall=_recall(n_tp, n_fn),
        entity_true_positives=e_tp,
        entity_false_positives=e_fp,
        entity_false_negatives=e_fn,
        entity_block_precision=_precision(e_tp, e_fp),
        entity_fabrication_recall=_recall(e_tp, e_fn),
    )


def load_numeric_corpus() -> list[dict]:
    """Load the committed hand-labeled grounding corpus."""
    data = json.loads((_CORPUS_DIR / "numeric_l3.json").read_text(encoding="utf-8"))
    return data["cases"]
