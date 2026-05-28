"""Layer 6 — validate the validator (BioForge v4 §4).

The grounding validator is software that can be wrong, so we measure it like any other
accuracy-critical component: a hand-labeled corpus of (response, tool_outputs,
expected_unsupported) cases, scored for **block precision** and **fabrication recall**.

These two numbers are first-class, release-gating metrics (see test_grounding_metrics.py)
and are what the platform's Accuracy Report surfaces for the numeric layer:
  - block precision — of the values the validator flagged, how many were truly
    unsupported. A false positive wrongly redacts real science and erodes trust.
  - fabrication recall — of the truly-unsupported values, how many were caught. A false
    negative is a fabrication that slipped through.

Ground truth is the set of numeric *values* a response asserts that cannot be traced to a
tool result. Comparing predicted-vs-expected by value means an extraction miss (a
fabrication the tokenizer never even extracted) correctly counts as a false negative — so
this corpus measures the whole pipeline (extraction + grounding), not just the matcher.
That directly addresses the validator's true recall ceiling.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from bioforge.agent.grounding.numeric import ground_response

_CORPUS_DIR = Path(__file__).parent / "corpus"


class CorpusMetrics(BaseModel):
    """Precision/recall of the numeric grounding layer over a labeled corpus."""

    n_cases: int
    true_positives: int = Field(description="Truly-unsupported values the validator flagged.")
    false_positives: int = Field(description="Grounded values the validator wrongly flagged (over-blocks).")
    false_negatives: int = Field(description="Truly-unsupported values the validator missed.")
    block_precision: float = Field(description="TP / (TP + FP). 1.0 = never over-blocks a real value.")
    fabrication_recall: float = Field(description="TP / (TP + FN). 1.0 = catches every labeled fabrication.")


def _round_key(x: float) -> float:
    return round(float(x), 9)


def evaluate_numeric_corpus(cases: list[dict]) -> CorpusMetrics:
    """Score the Layer-3 numeric validator against a labeled corpus."""
    tp = fp = fn = 0
    for case in cases:
        report = ground_response(case["response"], case["tool_outputs"])
        predicted = {_round_key(v.value) for v in report.unsupported}
        expected = {_round_key(x) for x in case.get("expected_unsupported", [])}
        tp += len(predicted & expected)
        fp += len(predicted - expected)
        fn += len(expected - predicted)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    return CorpusMetrics(
        n_cases=len(cases),
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        block_precision=precision,
        fabrication_recall=recall,
    )


def load_numeric_corpus() -> list[dict]:
    """Load the committed hand-labeled numeric-grounding corpus."""
    data = json.loads((_CORPUS_DIR / "numeric_l3.json").read_text(encoding="utf-8"))
    return data["cases"]
