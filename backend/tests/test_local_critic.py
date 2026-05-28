"""Tests for the Phase 5.3 LocalCritic role.

Same shape as test_local_planner / test_local_executor:
  - Protocol conformance.
  - last_usage populated.
  - Errors propagate (loop's _try_critique wraps them).
  - on_progress fires when set.
  - run_agent accepts a custom Critic — injection wires through.
"""

from __future__ import annotations

import pytest
from bioforge.agent.critic import CriticVerdict
from bioforge.agent.local_critic import LocalCritic
from bioforge.agent.planner import Plan, PlanStep
from bioforge.agent.roles import Critic, CriticContext


def _plan() -> Plan:
    return Plan(
        is_trivial=False,
        summary="...",
        steps=[PlanStep(idx=0, description="run gc_content", expected_tool="gc_content", rationale="r")],
    )


# --- Protocol conformance ----------------------------------------------------------


def test_local_critic_satisfies_critic_protocol(fake_llm_factory) -> None:
    critic = LocalCritic(fake_llm_factory([]))
    assert isinstance(critic, Critic)


# --- Happy path: ctx → CriticVerdict ----------------------------------------------


async def test_critique_returns_verdict_and_records_usage(
    fake_llm_factory, make_submit_verdict_response, passing_verdict
) -> None:
    llm = fake_llm_factory([make_submit_verdict_response(passing_verdict())])
    critic = LocalCritic(llm)
    assert critic.last_usage is None

    verdict = await critic.critique(
        CriticContext(
            goal="GC content of ATGC",
            plan=_plan(),
            response_text="GC content is 50%.",
            exec_steps=[],
            model="claude-sonnet-4-6",
        )
    )
    assert isinstance(verdict, CriticVerdict)
    assert verdict.satisfies_goal is True
    assert critic.last_usage is not None
    assert critic.last_usage.input_tokens > 0


async def test_critique_failing_verdict_carries_complaints(
    fake_llm_factory, make_submit_verdict_response, failing_verdict
) -> None:
    llm = fake_llm_factory([make_submit_verdict_response(failing_verdict(["Didn't actually call gc_content."]))])
    critic = LocalCritic(llm)

    verdict = await critic.critique(
        CriticContext(
            goal="GC content",
            plan=_plan(),
            response_text="I think it's around 50%.",
            exec_steps=[],
            model="claude-sonnet-4-6",
        )
    )
    assert verdict.satisfies_goal is False
    assert "Didn't actually call gc_content." in verdict.concrete_complaints


# --- on_progress hook -------------------------------------------------------------


async def test_on_progress_callback_fires(fake_llm_factory, make_submit_verdict_response, passing_verdict) -> None:
    llm = fake_llm_factory([make_submit_verdict_response(passing_verdict())])
    critic = LocalCritic(llm)

    messages: list[str] = []

    async def collect(msg: str) -> None:
        messages.append(msg)

    await critic.critique(
        CriticContext(
            goal="g",
            plan=_plan(),
            response_text="done.",
            exec_steps=[],
            model="claude-sonnet-4-6",
            on_progress=collect,
        )
    )
    assert len(messages) == 1
    assert "critiqu" in messages[0].lower()


# --- Errors propagate -------------------------------------------------------------


async def test_invalid_verdict_payload_raises_through(fake_llm_factory, make_submit_verdict_response) -> None:
    """LocalCritic does NOT swallow errors; the loop's _try_critique is the
    catch site. Same boundary contract as LocalPlanner."""
    llm = fake_llm_factory([make_submit_verdict_response({"satisfies_goal": "not-a-bool"})])
    critic = LocalCritic(llm)

    with pytest.raises(ValueError, match="invalid verdict"):
        await critic.critique(
            CriticContext(goal="g", plan=_plan(), response_text="x", exec_steps=[], model="claude-sonnet-4-6")
        )


async def test_critic_refusing_submit_verdict_raises_through(fake_llm_factory, make_text_response) -> None:
    llm = fake_llm_factory([make_text_response("I will not critique.")])
    critic = LocalCritic(llm)
    with pytest.raises(ValueError, match="did not call submit_verdict"):
        await critic.critique(
            CriticContext(goal="g", plan=_plan(), response_text="x", exec_steps=[], model="claude-sonnet-4-6")
        )


# --- Loop dispatch through injected Critic ----------------------------------------


async def test_run_agent_accepts_custom_critic(fake_llm_factory, make_submit_plan_response, trivial_plan) -> None:
    """If the planner returns a NON-trivial plan and the executor finishes,
    the critic is invoked. We inject a stub Critic that returns a passing
    verdict and verify the loop respects it."""
    from bioforge.agent import run_agent
    from bioforge.agent.llm import UsageSummary
    from bioforge.agent.roles import ExecutionResult, ExecutorContext

    class StubExecutor:
        def __init__(self):
            self.last_steps = []
            self.last_usage = UsageSummary.zero("claude-sonnet-4-6")
            self.last_status = "completed"

        async def execute(self, ctx: ExecutorContext) -> ExecutionResult:
            return ExecutionResult(response_text="GC=50%.", tool_calls=[], iterations_used=0)

    class StubCritic:
        def __init__(self):
            self.last_usage = UsageSummary.zero("claude-sonnet-4-6")
            self.calls = 0

        async def critique(self, ctx: CriticContext) -> CriticVerdict:
            self.calls += 1
            return CriticVerdict(satisfies_goal=True, reason="ok", concrete_complaints=[])

    # Force a NON-trivial plan so the critic fires (trivial plans skip critique).
    from tests.conftest import _multi_step_plan

    plan_dict = _multi_step_plan([("gc_content", "step 1"), ("gc_content", "step 2")])
    llm = fake_llm_factory([make_submit_plan_response(plan_dict)])
    critic = StubCritic()

    result = await run_agent(
        "do something",
        project_id="default-project",
        llm=llm,
        executor=StubExecutor(),
        critic=critic,
        enable_critic=True,
        skip_approval_gate=True,
    )

    assert critic.calls == 1
    assert result.status == "completed"
