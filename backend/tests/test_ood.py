"""§6 model-honesty layer: OOD input detection + model-uncertainty surfacing.

Deterministic, metadata-driven — the companions to L7 soundness. Unit-tested in
isolation, plus a wiring test that drives `_apply_grounding` directly so the verdict
record and the annotate-mode advisory are exercised end-to-end without a network call.
"""

from __future__ import annotations

import asyncio

import bioforge.tools  # noqa: F401 — ensure every tool is registered for REGISTRY lookups
import pytest
from bioforge.agent.grounding.ood import (
    OODReport,
    check_ood,
    collect_model_uncertainty,
    summarize_ood,
)
from bioforge.agent.loop import AgentStep, _apply_grounding
from bioforge.config import settings

# 20 nt protospacer (in the SpCas9 envelope) and an 18 nt truncated guide (out of it).
_G20 = "GAGTCCGAGCAGAAGAAGAA"
_G18 = "ACGTACGTACGTACGTAC"


# --- check_ood ----------------------------------------------------------------------


def test_check_ood_flags_non_20nt_find_offtargets_guide() -> None:
    report = check_ood([("find_offtargets", {"guide": _G18})])
    assert report.ok is False
    assert report.checked == 1
    assert len(report.flags) == 1
    flag = report.flags[0]
    assert flag.tool == "find_offtargets"
    assert flag.field == "guide"
    assert "18 nt" in flag.detail


def test_check_ood_passes_20nt_guide() -> None:
    report = check_ood([("find_offtargets", {"guide": _G20})])
    assert report.ok is True
    assert report.checked == 1
    assert report.flags == []


def test_check_ood_skips_unknown_tools_and_bad_input() -> None:
    # gc_content has no OOD checker; a non-dict input is ignored. Precision-first.
    report = check_ood([("gc_content", {"sequence": "ATGC"}), ("find_offtargets", None)])  # type: ignore[list-item]
    assert report.ok is True
    assert report.checked == 0
    assert report.flags == []


# --- collect_model_uncertainty ------------------------------------------------------


def test_collect_uncertainty_for_scored_tool() -> None:
    notes = collect_model_uncertainty(["score_guide_on_target"])
    keys = {n.score_key for n in notes}
    assert {"on_target", "deepcrispr"} <= keys
    on_target = next(n for n in notes if n.score_key == "on_target")
    assert "point estimate" in on_target.note.lower()


def test_collect_uncertainty_empty_for_pure_transform_and_unknown() -> None:
    assert collect_model_uncertainty(["gc_content"]) == []
    assert collect_model_uncertainty(["not_a_real_tool"]) == []


def test_collect_uncertainty_dedupes_repeated_tools() -> None:
    once = collect_model_uncertainty(["find_offtargets"])
    twice = collect_model_uncertainty(["find_offtargets", "find_offtargets"])
    assert once and len(once) == len(twice)


# --- summarize_ood ------------------------------------------------------------------


def test_summarize_ood_empty_when_no_flags() -> None:
    assert summarize_ood(OODReport(ok=True, checked=0, flags=[])) == ""


def test_summarize_ood_renders_flags() -> None:
    text = summarize_ood(check_ood([("find_offtargets", {"guide": _G18})]))
    assert "[BioForge OOD]" in text
    assert "find_offtargets.guide" in text


# --- loop wiring (via _apply_grounding) ---------------------------------------------


def _tool_step(name: str, tool_input: dict, tool_output: dict) -> AgentStep:
    return AgentStep(
        idx=0, type="tool_call", duration_ms=0, tool_name=name, tool_input=tool_input, tool_output=tool_output
    )


def test_apply_grounding_records_ood_and_uncertainty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "grounding_enabled", True)
    monkeypatch.setattr(settings, "grounding_mode", "annotate")
    monkeypatch.setattr(settings, "grounding_judge_enabled", False)
    steps = [_tool_step("find_offtargets", {"guide": _G18}, {"guide": _G18, "hits": []})]
    final_text, step, _usage = asyncio.run(
        _apply_grounding(
            goal="find off-targets",
            response_text="No off-targets were found.",
            steps=steps,
            status="completed",
            step_idx=1,
            llm=None,
            model="test-model",
        )
    )
    assert step is not None
    assert step.verdict["ood"]["ok"] is False
    assert any(f["tool"] == "find_offtargets" for f in step.verdict["ood"]["flags"])
    assert any(n["tool"] == "find_offtargets" for n in step.verdict["model_uncertainty"])
    assert "[BioForge OOD]" in final_text  # advisory appended in annotate mode


def test_apply_grounding_in_envelope_appends_no_ood(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "grounding_enabled", True)
    monkeypatch.setattr(settings, "grounding_mode", "annotate")
    monkeypatch.setattr(settings, "grounding_judge_enabled", False)
    steps = [_tool_step("find_offtargets", {"guide": _G20}, {"guide": _G20, "hits": []})]
    final_text, step, _usage = asyncio.run(
        _apply_grounding(
            goal="find off-targets",
            response_text="No off-targets were found.",
            steps=steps,
            status="completed",
            step_idx=1,
            llm=None,
            model="test-model",
        )
    )
    assert step is not None
    assert step.verdict["ood"]["ok"] is True
    assert "[BioForge OOD]" not in final_text
