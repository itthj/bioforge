from __future__ import annotations

import pytest

from bioforge.agent.critic import CriticVerdict, SUBMIT_VERDICT_TOOL, evaluate
from bioforge.agent.loop import AgentStep
from bioforge.agent.planner import Plan, PlanStep


def _fake_plan() -> Plan:
    return Plan(
        is_trivial=False,
        summary="Multi-step.",
        steps=[
            PlanStep(idx=0, description="rev comp", expected_tool="reverse_complement", rationale="x"),
            PlanStep(idx=1, description="gc", expected_tool="gc_content", rationale="y"),
        ],
    )


def _fake_exec_steps() -> list[AgentStep]:
    return [
        AgentStep(
            idx=0,
            type="tool_call",
            duration_ms=2,
            tool_name="reverse_complement",
            tool_input={"sequence": "ATGCATGC"},
            tool_output={"reverse_complement": "GCATGCAT", "length": 8},
        ),
        AgentStep(
            idx=1,
            type="tool_call",
            duration_ms=2,
            tool_name="gc_content",
            tool_input={"sequence": "GCATGCAT"},
            tool_output={"gc_percent": 50.0, "gc_count": 4, "total_length": 8, "n_count": 0},
        ),
    ]


async def test_critic_returns_passing_verdict(
    fake_llm_factory, make_submit_verdict_response, passing_verdict
) -> None:
    llm = fake_llm_factory([make_submit_verdict_response(passing_verdict())])

    result = await evaluate(
        goal="GC content of the reverse complement of ATGCATGC",
        plan=_fake_plan(),
        steps=_fake_exec_steps(),
        draft_response="The reverse complement is GCATGCAT and its GC content is 50%.",
        llm=llm,
        model="claude-sonnet-4-6",
    )

    assert isinstance(result.verdict, CriticVerdict)
    assert result.verdict.satisfies_goal is True
    assert result.verdict.concrete_complaints == []


async def test_critic_returns_failing_verdict_with_complaints(
    fake_llm_factory, make_submit_verdict_response, failing_verdict
) -> None:
    llm = fake_llm_factory(
        [
            make_submit_verdict_response(
                failing_verdict(
                    [
                        "Response reports GC content but not the reverse-complement sequence.",
                        "No citation of the tool versions used.",
                    ],
                    reason="Missing required outputs.",
                )
            )
        ]
    )

    result = await evaluate(
        goal="...",
        plan=_fake_plan(),
        steps=_fake_exec_steps(),
        draft_response="GC content is 50%.",
        llm=llm,
        model="claude-sonnet-4-6",
    )
    assert result.verdict.satisfies_goal is False
    assert len(result.verdict.concrete_complaints) == 2


async def test_critic_forces_submit_verdict_tool(
    fake_llm_factory, make_submit_verdict_response, passing_verdict
) -> None:
    llm = fake_llm_factory([make_submit_verdict_response(passing_verdict())])
    await evaluate(
        goal="x",
        plan=None,
        steps=[],
        draft_response="y",
        llm=llm,
        model="claude-sonnet-4-6",
    )
    call = llm.calls[0]
    assert call.tool_choice == {"type": "tool", "name": "submit_verdict"}
    assert call.tools == [SUBMIT_VERDICT_TOOL]


async def test_critic_raises_when_no_verdict_emitted(
    fake_llm_factory, make_text_response
) -> None:
    llm = fake_llm_factory([make_text_response("...")])
    with pytest.raises(ValueError, match="did not call submit_verdict"):
        await evaluate(
            goal="x",
            plan=None,
            steps=[],
            draft_response="y",
            llm=llm,
            model="claude-sonnet-4-6",
        )
