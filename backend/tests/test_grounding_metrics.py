"""Layer 6 — release-gating metrics for the numeric grounding validator (v4 §4, §13).

These assertions are the gate: the deterministic numeric layer must never over-block a
real value (precision 1.0) and must catch every labeled fabrication in the corpus
(recall 1.0). If a future corpus addition drops either number, that is a real signal,
not a flaky test — fix the validator, do not relax the gate silently.
"""

from __future__ import annotations

from bioforge.agent.grounding import (
    evaluate_numeric_corpus,
    load_numeric_corpus,
)

# Release-gating thresholds for the deterministic layer.
_PRECISION_GATE = 1.0
_RECALL_GATE = 1.0


def test_corpus_loads_and_is_well_formed() -> None:
    cases = load_numeric_corpus()
    assert len(cases) >= 10
    for c in cases:
        assert "response" in c
        assert "tool_outputs" in c
        assert isinstance(c["tool_outputs"], list)


def test_corpus_covers_both_grounded_and_fabricated() -> None:
    cases = load_numeric_corpus()
    assert any(c.get("expected_unsupported") for c in cases), "need at least one fabrication case"
    assert any(not c.get("expected_unsupported") for c in cases), "need at least one clean case"


def test_numeric_validator_meets_release_thresholds() -> None:
    metrics = evaluate_numeric_corpus(load_numeric_corpus())
    assert metrics.block_precision >= _PRECISION_GATE, metrics
    assert metrics.fabrication_recall >= _RECALL_GATE, metrics
    assert metrics.false_positives == 0, metrics
    assert metrics.true_positives >= 4, metrics
