"""Layer 7 execution-time soundness checks (BioForge v4 §4, §0).

Unit tests for the deterministic detector, plus one loop-integration test confirming the
soundness report is recorded on the `validation` trace step.
"""

from __future__ import annotations

import pytest
from bioforge.agent import run_agent
from bioforge.agent.grounding import check_soundness
from bioforge.config import settings
from bioforge.constants import DEFAULT_PROJECT_ID

# --- Detector unit tests ------------------------------------------------------------


def test_valid_values_are_sound() -> None:
    report = check_soundness([{"gc_percent": 55.0, "on_target_score": 0.82, "e_value": 2e-40}])
    assert report.ok
    assert report.checked == 3
    assert report.violations == []


def test_flags_percent_over_100() -> None:
    report = check_soundness([{"gc_percent": 150.0}])
    assert not report.ok
    (v,) = report.violations
    assert v.field == "gc_percent"
    assert v.value == 150.0
    assert v.bound == "[0, 100]"


def test_flags_unit_interval_overflow() -> None:
    report = check_soundness([{"guides": [{"on_target_score": 1.5}]}])
    assert not report.ok
    (v,) = report.violations
    assert v.field == "on_target_score"
    assert v.path == "guides[0].on_target_score"
    assert v.bound == "[0, 1]"


def test_flags_negative_evalue() -> None:
    report = check_soundness([{"e_value": -1.0}])
    assert not report.ok
    assert report.violations[0].bound == ">= 0"


def test_unknown_fields_are_left_alone() -> None:
    # bit_score has no certain bound -> not checked, not flagged.
    report = check_soundness([{"bit_score": 500.0, "accession": "NM_007294"}])
    assert report.ok
    assert report.checked == 0


def test_booleans_are_not_treated_as_numbers() -> None:
    report = check_soundness([{"destructive": True, "gc_percent": 50.0}])
    assert report.ok
    assert report.checked == 1  # only gc_percent, not the bool


# --- Loop integration ---------------------------------------------------------------


@pytest.fixture
def grounding_on(monkeypatch):
    monkeypatch.setattr(settings, "grounding_enabled", True)


async def test_soundness_recorded_in_validation_step(
    grounding_on,
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    trivial_plan,
) -> None:
    llm = fake_llm_factory(
        [
            make_submit_plan_response(trivial_plan(tool_name="gc_content")),
            make_tool_use_response("gc_content", {"sequence": "ATGCATGC"}),
            make_text_response("GC content of ATGCATGC is 50.0%."),
        ]
    )
    result = await run_agent("GC of ATGCATGC", project_id=DEFAULT_PROJECT_ID, llm=llm)

    validation = next(s for s in result.steps if s.type == "validation")
    soundness = validation.verdict["soundness"]
    assert soundness["ok"] is True
    assert soundness["checked"] >= 1  # gc_percent was bound-checked
