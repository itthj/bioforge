"""Agent loop tests, single-attempt path.

The loop runs PLAN → EXECUTE → (CRITIQUE → REPLAN once). For trivial plans, critique is
skipped. These tests cover trivial-plan flows; the replan path is exercised in
test_multi_step.py.
"""

from __future__ import annotations

import pytest
from bioforge.agent import run_agent
from bioforge.constants import DEFAULT_PROJECT_ID


async def test_happy_path_trivial_plan_then_tool_use_then_final(
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    trivial_plan,
) -> None:
    """Trivial plan → executor calls gc_content → executor returns text. No critic."""
    llm = fake_llm_factory(
        [
            make_submit_plan_response(trivial_plan(tool_name="gc_content")),
            make_tool_use_response(
                tool_name="gc_content",
                tool_input={"sequence": "ATGCATGC"},
                preamble_text="Computing GC content.",
            ),
            make_text_response(
                "GC content of ATGCATGC is 50.0% (4/8 bases). (tool: gc_content v1.0.0, Biopython gc_fraction)"
            ),
        ]
    )

    result = await run_agent(
        "What is the GC content of ATGCATGC?",
        project_id=DEFAULT_PROJECT_ID,
        llm=llm,
    )

    assert result.status == "completed"
    assert "50" in result.response_text

    step_types = [s.type for s in result.steps]
    assert step_types[0] == "plan"
    assert "tool_call" in step_types
    assert step_types[-1] == "final"
    assert "critique" not in step_types  # trivial → critic skipped

    tool_step = next(s for s in result.steps if s.type == "tool_call")
    assert tool_step.tool_name == "gc_content"
    assert tool_step.tool_output["gc_percent"] == pytest.approx(50.0)


async def test_planner_call_uses_submit_plan_tool_choice(
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    trivial_plan,
) -> None:
    llm = fake_llm_factory(
        [
            make_submit_plan_response(trivial_plan(tool_name="gc_content")),
            make_tool_use_response("gc_content", {"sequence": "ATGC"}),
            make_text_response("50%"),
        ]
    )
    await run_agent("GC of ATGC", project_id=DEFAULT_PROJECT_ID, llm=llm)

    planner_call, executor_call_1, *_ = llm.calls
    assert planner_call.tool_choice == {"type": "tool", "name": "submit_plan"}
    assert [t["name"] for t in planner_call.tools] == ["submit_plan"]

    # Executor sees bio tools, not submit_plan; cache_control on last entry.
    exec_tool_names = [t["name"] for t in executor_call_1.tools]
    assert "gc_content" in exec_tool_names
    assert "submit_plan" not in exec_tool_names
    assert executor_call_1.tools[-1].get("cache_control") == {"type": "ephemeral"}


async def test_planner_refusal_short_circuits_executor(fake_llm_factory, make_submit_plan_response) -> None:
    """Planner emits trivial=true, steps=[], summary explains missing capability →
    executor is bypassed; refusal returned directly. No fabrication."""
    refusal_summary = (
        "I can't do this. The goal requires BLAST (sequence alignment against a "
        "reference genome), which is not registered. Tools available: gc_content, "
        "reverse_complement. You could try the NCBI BLAST web interface."
    )
    llm = fake_llm_factory([make_submit_plan_response({"is_trivial": True, "summary": refusal_summary, "steps": []})])

    result = await run_agent(
        "BLAST this against the human genome: ATGCATGC",
        project_id=DEFAULT_PROJECT_ID,
        llm=llm,
    )

    assert result.status == "refused"
    assert result.response_text == refusal_summary
    assert all(s.type != "tool_call" for s in result.steps)
    # The planner is the only LLM call; executor and critic never ran.
    assert len(llm.calls) == 1


async def test_unknown_tool_call_in_executor_becomes_error_step(
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    trivial_plan,
) -> None:
    """If the executor hallucinates a call to an unregistered tool, the loop records the
    error and continues without crashing."""
    llm = fake_llm_factory(
        [
            make_submit_plan_response(trivial_plan(tool_name="gc_content")),
            make_tool_use_response("run_blast", {"query": "ATGC"}),  # not registered
            make_text_response("BLAST isn't available. Try gc_content instead."),
        ]
    )

    result = await run_agent("BLAST this", project_id=DEFAULT_PROJECT_ID, llm=llm)
    error_steps = [s for s in result.steps if s.type == "tool_error"]
    assert len(error_steps) == 1
    assert error_steps[0].tool_name == "run_blast"
    assert "not registered" in error_steps[0].error
    assert result.status == "completed"


async def test_iteration_cap_caught_in_executor(
    fake_llm_factory, make_submit_plan_response, make_tool_use_response, trivial_plan
) -> None:
    """Planner emits a trivial plan, then executor loops on tool_use past the cap."""
    looping = make_tool_use_response("gc_content", {"sequence": "ATGC"})
    llm = fake_llm_factory(
        [
            make_submit_plan_response(trivial_plan(tool_name="gc_content")),
            looping,
            looping,
        ]
    )

    result = await run_agent(
        "loop forever",
        project_id=DEFAULT_PROJECT_ID,
        llm=llm,
        max_iterations=2,
    )
    assert result.status == "iteration_cap"
    assert "iteration cap" in result.response_text.lower()


async def test_planner_failure_falls_back_gracefully(
    fake_llm_factory, make_text_response, make_tool_use_response
) -> None:
    """If the planner crashes (model returns text instead of submit_plan call), the loop
    records a plan-error step and proceeds with direct execution — no plan context."""
    llm = fake_llm_factory(
        [
            make_text_response("I refuse to plan."),  # planner fails — no submit_plan
            make_tool_use_response("gc_content", {"sequence": "ATGC"}),
            make_text_response("GC is 50%."),
        ]
    )
    result = await run_agent(
        "GC of ATGC",
        project_id=DEFAULT_PROJECT_ID,
        llm=llm,
        enable_critic=False,  # avoid critic noise in this fallback test
    )
    # First step is the plan with error recorded
    assert result.steps[0].type == "plan"
    assert result.steps[0].error is not None
    assert "did not call submit_plan" in result.steps[0].error
    # But execution still proceeded
    assert any(s.type == "tool_call" and s.tool_name == "gc_content" for s in result.steps)
    assert result.status == "completed"
