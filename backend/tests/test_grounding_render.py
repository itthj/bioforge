"""Scientist-facing grounding rendering + annotate mode (BioForge v4 §4).

The renderer turns a ValidationReport into a legible trust signal; annotate mode appends
it to the response without removing anything (in contrast to enforce).
"""

from __future__ import annotations

import pytest
from bioforge.agent import run_agent
from bioforge.agent.grounding import ground_response, summarize_grounding
from bioforge.config import settings
from bioforge.constants import DEFAULT_PROJECT_ID

# --- Renderer unit tests ------------------------------------------------------------


def test_affirms_when_all_claims_grounded() -> None:
    report = ground_response("On-target 0.78 for rs80357065.", [{"on_target_score": 0.78, "id": "rs80357065"}])
    summary = summarize_grounding(report)
    assert "all claims traced to tool results" in summary
    assert "1 numeric" in summary
    assert "1 identifier" in summary


def test_flags_unsupported_claims() -> None:
    report = ground_response("The on-target score is 0.92.", [{"on_target_score": 0.78}])
    summary = summarize_grounding(report)
    assert "could not be traced" in summary
    assert "0.92" in summary


def test_empty_when_no_quantitative_or_identifier_claims() -> None:
    # A purely qualitative answer should not be cluttered with a grounding badge.
    report = ground_response("The Cas9 guide was designed against the locus.", [{"note": "done"}])
    assert summarize_grounding(report) == ""


# --- Annotate mode in the loop ------------------------------------------------------


@pytest.fixture
def grounding_annotate(monkeypatch):
    monkeypatch.setattr(settings, "grounding_enabled", True)
    monkeypatch.setattr(settings, "grounding_mode", "annotate")


def _script(
    fake_llm_factory, make_submit_plan_response, make_tool_use_response, make_text_response, trivial_plan, text
):
    return fake_llm_factory(
        [
            make_submit_plan_response(trivial_plan(tool_name="gc_content")),
            make_tool_use_response("gc_content", {"sequence": "ATGCATGC"}),
            make_text_response(text),
        ]
    )


async def test_annotate_flags_without_redacting(
    grounding_annotate,
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    trivial_plan,
) -> None:
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

    body = result.response_text
    assert body.startswith(final_text)  # original text untouched...
    assert "0.92" in body  # ...the fabricated value is NOT removed
    assert "[unverifiable]" not in body  # annotate never redacts
    assert "Grounding check" in body  # ...but it IS flagged for the reader
    assert "could not be traced" in body

    validation = next(s for s in result.steps if s.type == "validation")
    assert validation.verdict["mode"] == "annotate"
    assert validation.verdict["enforced"] is False


async def test_annotate_affirms_a_grounded_response(
    grounding_annotate,
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

    body = result.response_text
    assert body.startswith(final_text)
    assert "Grounding check: all claims traced to tool results" in body
