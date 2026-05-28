"""Shadow-mode wiring of the Layer-3 grounding validator into the agent loop.

Contract:
  - Default OFF: the loop is behaviorally unchanged — no `validation` step appears.
  - When BIOFORGE_GROUNDING_ENABLED is set, every real response gets a `validation`
    trace step carrying the numeric-grounding report, but the response text is NEVER
    altered (shadow mode — enforcement is a later slice).
"""

from __future__ import annotations

import pytest
from bioforge.agent import run_agent
from bioforge.config import settings
from bioforge.constants import DEFAULT_PROJECT_ID


@pytest.fixture
def grounding_on(monkeypatch):
    """Enable shadow grounding for the duration of one test (auto-reverted)."""
    monkeypatch.setattr(settings, "grounding_enabled", True)


def _script(
    fake_llm_factory, make_submit_plan_response, make_tool_use_response, make_text_response, trivial_plan, final_text
):
    return fake_llm_factory(
        [
            make_submit_plan_response(trivial_plan(tool_name="gc_content")),
            make_tool_use_response("gc_content", {"sequence": "ATGCATGC"}),
            make_text_response(final_text),
        ]
    )


async def test_no_validation_step_when_disabled(
    fake_llm_factory, make_submit_plan_response, make_tool_use_response, make_text_response, trivial_plan
) -> None:
    # gc_content of ATGCATGC is 50.0% — a grounded statement, but the flag is OFF.
    llm = _script(
        fake_llm_factory,
        make_submit_plan_response,
        make_tool_use_response,
        make_text_response,
        trivial_plan,
        "GC content of ATGCATGC is 50.0%.",
    )
    result = await run_agent("GC of ATGCATGC", project_id=DEFAULT_PROJECT_ID, llm=llm)
    assert result.status == "completed"
    assert all(s.type != "validation" for s in result.steps)


async def test_shadow_grounding_records_grounded_response(
    grounding_on, fake_llm_factory, make_submit_plan_response, make_tool_use_response, make_text_response, trivial_plan
) -> None:
    final_text = "GC content of ATGCATGC is 50.0%."
    llm = _script(
        fake_llm_factory,
        make_submit_plan_response,
        make_tool_use_response,
        make_text_response,
        trivial_plan,
        final_text,
    )
    result = await run_agent("GC of ATGCATGC", project_id=DEFAULT_PROJECT_ID, llm=llm)

    validation = next(s for s in result.steps if s.type == "validation")
    assert validation.verdict["ok"] is True
    assert validation.verdict["layer"] == "L3_numeric"
    # Shadow mode: the response text is untouched.
    assert result.response_text == final_text


async def test_shadow_grounding_flags_fabricated_number(
    grounding_on, fake_llm_factory, make_submit_plan_response, make_tool_use_response, make_text_response, trivial_plan
) -> None:
    # 50.0% is grounded (gc_content); the 0.92 CFD score is fabricated — no such tool ran.
    final_text = "GC content is 50.0%, with an off-target CFD score of 0.92."
    llm = _script(
        fake_llm_factory,
        make_submit_plan_response,
        make_tool_use_response,
        make_text_response,
        trivial_plan,
        final_text,
    )
    result = await run_agent("GC of ATGCATGC", project_id=DEFAULT_PROJECT_ID, llm=llm)

    validation = next(s for s in result.steps if s.type == "validation")
    assert validation.verdict["ok"] is False
    unsupported = [c for c in validation.verdict["numeric_claims"] if c["status"] == "unsupported"]
    assert any(c["value"] == 0.92 for c in unsupported)
    # Shadow mode does NOT redact — the fabricated value still reaches the user this slice.
    assert "0.92" in result.response_text
    assert result.response_text == final_text


async def test_validation_step_is_last_when_enabled(
    grounding_on, fake_llm_factory, make_submit_plan_response, make_tool_use_response, make_text_response, trivial_plan
) -> None:
    llm = _script(
        fake_llm_factory,
        make_submit_plan_response,
        make_tool_use_response,
        make_text_response,
        trivial_plan,
        "GC content of ATGCATGC is 50.0%.",
    )
    result = await run_agent("GC of ATGCATGC", project_id=DEFAULT_PROJECT_ID, llm=llm)
    assert result.steps[-1].type == "validation"


# --- Enforce mode (visible redaction + audit note) -----------------------------------


@pytest.fixture
def grounding_enforce(monkeypatch):
    """Enable grounding in enforce mode for one test (auto-reverted)."""
    monkeypatch.setattr(settings, "grounding_enabled", True)
    monkeypatch.setattr(settings, "grounding_mode", "enforce")


async def test_enforce_redacts_fabricated_number(
    grounding_enforce,
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    trivial_plan,
) -> None:
    # 50.0% is grounded (gc_content); 0.92 is fabricated -> only 0.92 is redacted.
    final_text = "GC content is 50.0%, with an off-target CFD score of 0.92."
    llm = _script(
        fake_llm_factory,
        make_submit_plan_response,
        make_tool_use_response,
        make_text_response,
        trivial_plan,
        final_text,
    )
    result = await run_agent("GC of ATGCATGC", project_id=DEFAULT_PROJECT_ID, llm=llm)

    validation = next(s for s in result.steps if s.type == "validation")
    assert validation.verdict["ok"] is False
    assert validation.verdict["enforced"] is True

    body = result.response_text
    assert "score of [unverifiable]" in body  # fabricated value redacted in place
    assert "50.0%" in body  # grounded value preserved
    assert "[BioForge grounding]" in body  # audit footer present
    assert body != final_text


async def test_enforce_leaves_fully_grounded_response_untouched(
    grounding_enforce,
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    trivial_plan,
) -> None:
    final_text = "GC content of ATGCATGC is 50.0%."
    llm = _script(
        fake_llm_factory,
        make_submit_plan_response,
        make_tool_use_response,
        make_text_response,
        trivial_plan,
        final_text,
    )
    result = await run_agent("GC of ATGCATGC", project_id=DEFAULT_PROJECT_ID, llm=llm)

    validation = next(s for s in result.steps if s.type == "validation")
    assert validation.verdict["ok"] is True
    assert validation.verdict["enforced"] is False
    assert result.response_text == final_text
    assert "[unverifiable]" not in result.response_text


async def test_shadow_records_but_does_not_enforce(
    grounding_on,
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    trivial_plan,
) -> None:
    # Enabled but mode defaults to shadow: a fabrication is recorded, never redacted.
    final_text = "GC content is 50.0%, with a CFD of 0.92."
    llm = _script(
        fake_llm_factory,
        make_submit_plan_response,
        make_tool_use_response,
        make_text_response,
        trivial_plan,
        final_text,
    )
    result = await run_agent("GC of ATGCATGC", project_id=DEFAULT_PROJECT_ID, llm=llm)

    validation = next(s for s in result.steps if s.type == "validation")
    assert validation.verdict["ok"] is False
    assert validation.verdict["enforced"] is False
    assert result.response_text == final_text


# --- L4 entity/mechanistic judge wiring ----------------------------------------------


@pytest.fixture
def grounding_judge_enforce(monkeypatch):
    """Enable grounding + the L4 judge in enforce mode for one test."""
    monkeypatch.setattr(settings, "grounding_enabled", True)
    monkeypatch.setattr(settings, "grounding_judge_enabled", True)
    monkeypatch.setattr(settings, "grounding_mode", "enforce")


async def test_judge_flags_unsupported_mechanistic_claim(
    grounding_judge_enforce,
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    make_submit_grounding_response,
    trivial_plan,
) -> None:
    final_text = "GC content of ATGCATGC is 50.0%. This region disrupts the binding domain."
    llm = fake_llm_factory(
        [
            make_submit_plan_response(trivial_plan(tool_name="gc_content")),
            make_tool_use_response("gc_content", {"sequence": "ATGCATGC"}),
            make_text_response(final_text),
            make_submit_grounding_response(
                [{"text": "disrupts the binding domain", "kind": "mechanistic", "status": "unsupported"}]
            ),
        ]
    )
    result = await run_agent("GC of ATGCATGC", project_id=DEFAULT_PROJECT_ID, llm=llm)

    validation = next(s for s in result.steps if s.type == "validation")
    assert validation.verdict["ok"] is False
    assert any(c["kind"] == "mechanistic" and c["status"] == "unsupported" for c in validation.verdict["judged_claims"])
    body = result.response_text
    assert "50.0%" in body  # grounded numeric preserved
    assert "[BioForge grounding]" in body  # audit footer present
    assert "disrupts the binding domain" in body  # flagged in the footer


async def test_judge_clean_response_untouched(
    grounding_judge_enforce,
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    make_submit_grounding_response,
    trivial_plan,
) -> None:
    final_text = "GC content of ATGCATGC is 50.0%."
    llm = fake_llm_factory(
        [
            make_submit_plan_response(trivial_plan(tool_name="gc_content")),
            make_tool_use_response("gc_content", {"sequence": "ATGCATGC"}),
            make_text_response(final_text),
            make_submit_grounding_response([]),  # no entity/mechanistic claims found
        ]
    )
    result = await run_agent("GC of ATGCATGC", project_id=DEFAULT_PROJECT_ID, llm=llm)

    validation = next(s for s in result.steps if s.type == "validation")
    assert validation.verdict["ok"] is True
    assert result.response_text == final_text


async def test_judge_not_called_when_disabled(
    grounding_on,  # grounding enabled, judge NOT enabled
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    trivial_plan,
) -> None:
    # Only 3 scripted responses: if the judge made a model call it would demand a 4th and
    # FakeLLM would raise. Passing proves the judge stayed silent when its flag is off.
    final_text = "GC content of ATGCATGC is 50.0%."
    llm = _script(
        fake_llm_factory,
        make_submit_plan_response,
        make_tool_use_response,
        make_text_response,
        trivial_plan,
        final_text,
    )
    result = await run_agent("GC of ATGCATGC", project_id=DEFAULT_PROJECT_ID, llm=llm)

    validation = next(s for s in result.steps if s.type == "validation")
    assert validation.verdict["judged_claims"] == []
    assert len(llm.calls) == 3  # plan, executor tool-use, executor final — no judge call
