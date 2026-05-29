"""Layer 6 — release-gating metrics for the deterministic grounding layers (v4 §4, §13).

These assertions are the gate: the deterministic numeric AND identifier layers must never
over-block a real value/id (precision 1.0) and must catch every labeled fabrication in the
corpus (recall 1.0). If a future corpus addition drops either number, that is a real
signal, not a flaky test — fix the validator, do not relax the gate silently.
"""

from __future__ import annotations

from bioforge.agent.grounding import evaluate_corpus, load_numeric_corpus

_GATE = 1.0


def test_corpus_loads_and_is_well_formed() -> None:
    cases = load_numeric_corpus()
    assert len(cases) >= 12
    for c in cases:
        assert "response" in c
        assert "tool_outputs" in c
        assert isinstance(c["tool_outputs"], list)


def test_corpus_covers_numeric_and_identifier_cases() -> None:
    cases = load_numeric_corpus()
    assert any(c.get("expected_unsupported") for c in cases), "need a fabricated numeric case"
    assert any(c.get("expected_unsupported_ids") for c in cases), "need a fabricated identifier case"
    assert any(c.get("extra_sources") for c in cases), "need an echoed-from-input case"
    assert any(not c.get("expected_unsupported") and not c.get("expected_unsupported_ids") for c in cases), (
        "need a fully-grounded case"
    )


def test_numeric_layer_meets_release_thresholds() -> None:
    m = evaluate_corpus(load_numeric_corpus())
    assert m.numeric_block_precision >= _GATE, m
    assert m.numeric_fabrication_recall >= _GATE, m
    assert m.numeric_false_positives == 0, m
    assert m.numeric_true_positives >= 4, m


def test_identifier_layer_meets_release_thresholds() -> None:
    m = evaluate_corpus(load_numeric_corpus())
    assert m.entity_block_precision >= _GATE, m
    assert m.entity_fabrication_recall >= _GATE, m
    assert m.entity_false_positives == 0, m
    assert m.entity_true_positives >= 2, m
