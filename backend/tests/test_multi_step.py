"""Integration tests for the full plan → execute → critique → replan loop.

These exercise the path the planner/critic/replan slice was built to prove:
  - A non-trivial goal gets a multi-step plan.
  - The executor chains two real tools (reverse_complement → gc_content).
  - The critic evaluates and either passes or triggers a replan.
  - On replan, complaints from the verdict flow into the new attempt.
"""

from __future__ import annotations

import pytest
from bioforge.agent import run_agent
from bioforge.constants import DEFAULT_PROJECT_ID


async def test_multi_step_plan_then_two_tools_then_critic_passes(
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    make_submit_verdict_response,
    multi_step_plan,
    passing_verdict,
) -> None:
    """The canonical multi-step demo: GC content of the reverse complement of ATGCATGC.

    Plan: reverse_complement → gc_content.
    Executor calls each in turn, summarizes.
    Critic approves.
    """
    plan = multi_step_plan(
        [
            ("reverse_complement", "Compute reverse complement of the input sequence."),
            (
                "gc_content",
                "Compute GC content on the reverse-complemented sequence.",
            ),
        ],
        summary="Reverse-complement first, then compute GC of the result.",
    )

    llm = fake_llm_factory(
        [
            # 1. Planner
            make_submit_plan_response(plan),
            # 2. Executor: call reverse_complement
            make_tool_use_response(
                tool_name="reverse_complement",
                tool_input={"sequence": "ATGCATGC"},
                tool_use_id="toolu_rc",
            ),
            # 3. Executor: call gc_content on the result
            make_tool_use_response(
                tool_name="gc_content",
                tool_input={"sequence": "GCATGCAT"},
                tool_use_id="toolu_gc",
            ),
            # 4. Executor: final text
            make_text_response(
                "The reverse complement of ATGCATGC is GCATGCAT. Its GC content is 50.0% "
                "(4/8 bases). Tools: reverse_complement v1.0.0, gc_content v1.0.0."
            ),
            # 5. Critic: approve
            make_submit_verdict_response(passing_verdict()),
        ]
    )

    result = await run_agent(
        "What is the GC content of the reverse complement of ATGCATGC?",
        project_id=DEFAULT_PROJECT_ID,
        llm=llm,
    )

    assert result.status == "completed"
    assert "GCATGCAT" in result.response_text
    assert "50" in result.response_text

    step_types = [s.type for s in result.steps]
    assert step_types[0] == "plan"
    tool_calls = [s for s in result.steps if s.type == "tool_call"]
    assert [s.tool_name for s in tool_calls] == ["reverse_complement", "gc_content"]
    assert tool_calls[0].tool_output["reverse_complement"] == "GCATGCAT"
    assert tool_calls[1].tool_output["gc_percent"] == pytest.approx(50.0)
    assert any(s.type == "critique" for s in result.steps)


async def test_critic_failure_triggers_replan_then_passes(
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    make_submit_verdict_response,
    multi_step_plan,
    trivial_plan,
    failing_verdict,
    passing_verdict,
) -> None:
    """First attempt only computes GC and skips reverse_complement. Critic complains.
    Replan covers both. Critic approves the second attempt."""

    initial_plan = multi_step_plan(
        [
            ("reverse_complement", "Reverse complement of the input."),
            ("gc_content", "GC content of the reverse-complemented sequence."),
        ]
    )
    revised_plan = multi_step_plan(
        [
            ("reverse_complement", "Reverse complement (was missed last time)."),
            ("gc_content", "GC content of the reverse complement."),
        ],
        summary="Cover the missed reverse_complement step.",
    )

    llm = fake_llm_factory(
        [
            # Attempt 1
            make_submit_plan_response(initial_plan),  # planner
            make_tool_use_response(  # executor wrongly skips rev_comp, goes straight to GC
                "gc_content", {"sequence": "ATGCATGC"}
            ),
            make_text_response("GC content of ATGCATGC is 50%."),
            make_submit_verdict_response(  # critic complains
                failing_verdict(
                    [
                        "The plan called for reverse_complement before gc_content; this step was skipped.",
                        "The response does not mention the reverse-complemented sequence.",
                    ]
                )
            ),
            # Replan + attempt 2
            make_submit_plan_response(revised_plan),
            make_tool_use_response("reverse_complement", {"sequence": "ATGCATGC"}, tool_use_id="toolu_rc2"),
            make_tool_use_response("gc_content", {"sequence": "GCATGCAT"}, tool_use_id="toolu_gc2"),
            make_text_response(
                "Reverse complement: GCATGCAT. GC content: 50.0%. "
                "Tools used: reverse_complement v1.0.0, gc_content v1.0.0."
            ),
            make_submit_verdict_response(passing_verdict()),
        ]
    )

    result = await run_agent(
        "GC content of the reverse complement of ATGCATGC",
        project_id=DEFAULT_PROJECT_ID,
        llm=llm,
    )

    assert result.status == "completed_after_replan"

    step_types = [s.type for s in result.steps]
    assert step_types.count("plan") == 1
    assert step_types.count("replan") == 1
    assert step_types.count("critique") == 2

    # Replan step carries the revised plan
    replan_step = next(s for s in result.steps if s.type == "replan")
    assert replan_step.plan is not None
    assert len(replan_step.plan["steps"]) == 2

    # First critique recorded the failure
    critiques = [s for s in result.steps if s.type == "critique"]
    assert critiques[0].verdict["satisfies_goal"] is False
    assert len(critiques[0].verdict["concrete_complaints"]) == 2
    assert critiques[1].verdict["satisfies_goal"] is True


async def test_critique_failed_status_when_both_attempts_rejected(
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    make_submit_verdict_response,
    multi_step_plan,
    failing_verdict,
) -> None:
    plan = multi_step_plan(
        [("reverse_complement", "x"), ("gc_content", "y")],
    )
    bad_verdict = failing_verdict(["Still doesn't address reverse-complement step."])

    llm = fake_llm_factory(
        [
            make_submit_plan_response(plan),
            make_tool_use_response("gc_content", {"sequence": "ATGC"}),
            make_text_response("50%"),
            make_submit_verdict_response(bad_verdict),
            # replan
            make_submit_plan_response(plan),
            make_tool_use_response("gc_content", {"sequence": "ATGC"}),
            make_text_response("Still 50%."),
            make_submit_verdict_response(bad_verdict),
        ]
    )

    result = await run_agent("GC of rev comp of ATGC", project_id=DEFAULT_PROJECT_ID, llm=llm)

    assert result.status == "critique_failed"
    assert "Remaining concerns" in result.response_text
    assert "reverse-complement" in result.response_text.lower()


async def test_complaints_flow_into_replan_planner_prompt(
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    make_submit_verdict_response,
    multi_step_plan,
    failing_verdict,
    passing_verdict,
) -> None:
    """Critic complaints must appear in the user message sent to the replan planner —
    otherwise the replan is uninformed."""
    plan = multi_step_plan([("gc_content", "")])
    llm = fake_llm_factory(
        [
            make_submit_plan_response(plan),
            make_tool_use_response("gc_content", {"sequence": "ATGC"}),
            make_text_response("50%"),
            make_submit_verdict_response(failing_verdict(["Missed reverse complement step", "No citations"])),
            make_submit_plan_response(plan),  # replan
            make_tool_use_response("gc_content", {"sequence": "ATGC"}),
            make_text_response("50% (gc_content v1.0.0)"),
            make_submit_verdict_response(passing_verdict()),
        ]
    )

    await run_agent("x", project_id=DEFAULT_PROJECT_ID, llm=llm)

    # Calls: 0=planner, 1-2=executor1, 3=critic1, 4=replanner, 5-6=executor2, 7=critic2
    replanner_call = llm.calls[4]
    replanner_user_content = replanner_call.messages[0]["content"]
    assert "Missed reverse complement step" in replanner_user_content
    assert "No citations" in replanner_user_content
    # Also the executor's user message in attempt 2 should carry the complaints
    executor2_call = llm.calls[5]
    assert "Missed reverse complement step" in executor2_call.messages[0]["content"]
