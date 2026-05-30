"""§0/§4.1 execution-time soundness gate (`soundness_refusal`).

In "block" mode an impossible tool output (a value outside a known physical bound) is
rejected before it feeds downstream steps; "off" (default) preserves behavior (the
post-response detector still records violations). Loop equivalence is covered by the full
suite staying green at the default "off"; the inline wiring mirrors the existing tool_error path.
"""

from __future__ import annotations

from bioforge.agent.grounding import soundness_refusal


def test_block_rejects_out_of_bounds_output() -> None:
    report = soundness_refusal({"gc_percent": 150.0}, mode="block")
    assert report is not None
    assert report.violations
    assert report.violations[0].field == "gc_percent"


def test_off_mode_never_gates() -> None:
    assert soundness_refusal({"gc_percent": 150.0}, mode="off") is None


def test_block_allows_sound_output() -> None:
    assert soundness_refusal({"gc_percent": 42.0, "mit_score": 0.3, "cfd_score": 0.9}, mode="block") is None


def test_block_ignores_unbounded_fields() -> None:
    # Precision-first: a field with no known bound is never rejected on a guessed range.
    assert soundness_refusal({"some_count": 9999, "name": "x"}, mode="block") is None
