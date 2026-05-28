"""Tests for the Phase 5.1 LocalPlanner role.

What's exercised here:
  - LocalPlanner.make_plan returns a Plan from a PlanContext (the Protocol shape).
  - It pre-pends `previous_complaints` to the goal so the planner LLM gets the
    same "revised plan that addresses each issue" framing the old in-loop
    `_try_plan` used.
  - `last_usage` is populated after each successful call.
  - `on_progress` is invoked when set.
  - It satisfies the Planner Protocol via runtime isinstance.
  - Errors propagate (the loop's `_try_plan` wrapper is what catches them).

Behavioral equivalence with the previous in-loop planner is also covered: a
loop-level dispatch test in `test_agent_run.py` confirms run_agent still
produces the same trace when LocalPlanner is the default.
"""

from __future__ import annotations

import pytest
from bioforge.agent.local_planner import LocalPlanner
from bioforge.agent.planner import Plan
from bioforge.agent.roles import PlanContext, Planner
from bioforge.tools.registry import list_tools

# --- Protocol conformance ----------------------------------------------------------


def test_local_planner_satisfies_planner_protocol(fake_llm_factory) -> None:
    """Sanity: LocalPlanner is a structural Planner."""
    planner = LocalPlanner(fake_llm_factory([]))
    assert isinstance(planner, Planner)


# --- Happy path: ctx → Plan, usage captured ----------------------------------------


async def test_make_plan_returns_validated_plan_from_context(
    fake_llm_factory, make_submit_plan_response, multi_step_plan
) -> None:
    plan_dict = multi_step_plan(
        [
            ("reverse_complement", "Compute the reverse complement first."),
            ("gc_content", "Compute GC content of the result."),
        ],
        summary="rev_comp then GC.",
    )
    llm = fake_llm_factory([make_submit_plan_response(plan_dict)])
    planner = LocalPlanner(llm)

    ctx = PlanContext(
        goal="GC content of the reverse complement of ATGCATGC",
        model="claude-sonnet-4-6",
        available_tools=list_tools(),
    )
    plan = await planner.make_plan(ctx)

    assert isinstance(plan, Plan)
    assert plan.is_trivial is False
    assert [s.expected_tool for s in plan.steps] == ["reverse_complement", "gc_content"]


async def test_make_plan_records_last_usage(fake_llm_factory, make_submit_plan_response, trivial_plan) -> None:
    """The loop reads last_usage to merge planner tokens into the run's total. Must
    be set after each successful call."""
    llm = fake_llm_factory([make_submit_plan_response(trivial_plan(tool_name="gc_content"))])
    planner = LocalPlanner(llm)
    assert planner.last_usage is None

    await planner.make_plan(
        PlanContext(goal="GC content of ATGC", model="claude-sonnet-4-6", available_tools=list_tools())
    )

    assert planner.last_usage is not None
    assert planner.last_usage.input_tokens > 0


# --- Previous complaints → revised goal --------------------------------------------


async def test_previous_complaints_are_prepended_to_goal(
    fake_llm_factory, make_submit_plan_response, trivial_plan
) -> None:
    """The planner LLM should see "previous attempt failed because..." so it can
    produce a revised plan. This mirrors the Phase 0-4 _try_plan behavior."""
    llm = fake_llm_factory([make_submit_plan_response(trivial_plan(tool_name="gc_content"))])
    planner = LocalPlanner(llm)

    ctx = PlanContext(
        goal="GC content of ATGC",
        model="claude-sonnet-4-6",
        available_tools=list_tools(),
        previous_complaints=[
            "First attempt didn't call any tool.",
            "Response was a guess, not a computation.",
        ],
    )
    await planner.make_plan(ctx)

    # Inspect what the FakeLLM was sent: messages[0].content should carry the
    # revised goal preamble.
    sent_messages = llm.calls[0].messages
    user_msg = sent_messages[0]["content"]
    assert "GC content of ATGC" in user_msg
    assert "The previous attempt failed because" in user_msg
    assert "First attempt didn't call any tool." in user_msg
    assert "produce a revised plan" in user_msg.lower()


async def test_no_complaints_means_unmodified_goal(fake_llm_factory, make_submit_plan_response, trivial_plan) -> None:
    """First plan (empty previous_complaints) must NOT include the failure preamble."""
    llm = fake_llm_factory([make_submit_plan_response(trivial_plan(tool_name="gc_content"))])
    planner = LocalPlanner(llm)
    ctx = PlanContext(
        goal="GC content of ATGC",
        model="claude-sonnet-4-6",
        available_tools=list_tools(),
    )
    await planner.make_plan(ctx)

    user_msg = llm.calls[0].messages[0]["content"]
    assert "GC content of ATGC" in user_msg
    assert "previous attempt failed" not in user_msg.lower()


# --- on_progress hook --------------------------------------------------------------


async def test_on_progress_callback_fires_once(fake_llm_factory, make_submit_plan_response, trivial_plan) -> None:
    """When ctx.on_progress is set, the planner emits a short heartbeat. Streaming
    clients use this to confirm the planner is alive between request and response."""
    llm = fake_llm_factory([make_submit_plan_response(trivial_plan(tool_name="gc_content"))])
    planner = LocalPlanner(llm)

    messages: list[str] = []

    async def collect(msg: str) -> None:
        messages.append(msg)

    await planner.make_plan(
        PlanContext(
            goal="GC content of ATGC",
            model="claude-sonnet-4-6",
            available_tools=list_tools(),
            on_progress=collect,
        )
    )
    assert len(messages) == 1
    assert "planning" in messages[0].lower() or "route" in messages[0].lower()


async def test_on_progress_callback_signals_replan_path(
    fake_llm_factory, make_submit_plan_response, trivial_plan
) -> None:
    """A heartbeat fired during replan should mention it's a re-plan so the UI can
    distinguish 'planning…' from 'replanning…' states."""
    llm = fake_llm_factory([make_submit_plan_response(trivial_plan(tool_name="gc_content"))])
    planner = LocalPlanner(llm)

    messages: list[str] = []

    async def collect(msg: str) -> None:
        messages.append(msg)

    await planner.make_plan(
        PlanContext(
            goal="GC content of ATGC",
            model="claude-sonnet-4-6",
            available_tools=list_tools(),
            previous_complaints=["the first attempt failed"],
            on_progress=collect,
        )
    )
    assert len(messages) == 1
    assert "re-plan" in messages[0].lower() or "replann" in messages[0].lower()


# --- Errors propagate (loop's _try_plan handles the wrapping) ----------------------


async def test_invalid_planner_payload_raises_through(fake_llm_factory, make_submit_plan_response) -> None:
    """LocalPlanner must NOT swallow planner errors. The loop's _try_plan wrapper is
    where errors get converted to AgentSteps — keeping that boundary clear means we
    can drop a different Planner impl in without re-implementing error handling."""
    llm = fake_llm_factory([make_submit_plan_response({"is_trivial": "not-a-bool"})])
    planner = LocalPlanner(llm)

    with pytest.raises(ValueError, match="invalid Plan"):
        await planner.make_plan(PlanContext(goal="g", model="claude-sonnet-4-6", available_tools=list_tools()))


async def test_model_refusing_submit_plan_raises_through(fake_llm_factory, make_text_response) -> None:
    """If the planner LLM emits text instead of calling submit_plan, the underlying
    error surfaces. LocalPlanner doesn't try to recover — the loop decides what to do."""
    llm = fake_llm_factory([make_text_response("I refuse to plan.")])
    planner = LocalPlanner(llm)

    with pytest.raises(ValueError, match="did not call submit_plan"):
        await planner.make_plan(PlanContext(goal="g", model="claude-sonnet-4-6", available_tools=list_tools()))


# --- LocalPlanner is injectable into run_agent -------------------------------------


async def test_run_agent_accepts_custom_planner(
    fake_llm_factory,
    make_submit_plan_response,
    make_text_response,
    trivial_plan,
) -> None:
    """The loop must dispatch through whatever Planner is injected. A stub Planner
    that returns a fixed Plan should drive run_agent's execution path — proves the
    Phase 5.1 injection point is wired."""
    from bioforge.agent import run_agent
    from bioforge.agent.llm import UsageSummary

    class StubPlanner:
        """A Planner that returns a hand-built Plan without an LLM round-trip."""

        def __init__(self) -> None:
            self.last_usage: UsageSummary | None = None
            self.calls = 0

        async def make_plan(self, ctx: PlanContext) -> Plan:
            self.calls += 1
            self.last_usage = UsageSummary.zero(ctx.model)
            return Plan.model_validate(trivial_plan(tool_name="gc_content"))

    # The executor still needs an LLM; planner is injected so it never fires the LLM.
    # Sequence the LLM responses for the executor + critic stages.
    llm = fake_llm_factory(
        [
            # Executor's first turn: call gc_content directly.
            # The conftest helper for tool_use produces a one-tool-call response.
            __import__("tests.conftest", fromlist=["_build_tool_use_response"])._build_tool_use_response(
                "gc_content", {"sequence": "ATGCATGC"}, tool_use_id="toolu_1"
            ),
            # Executor's second turn: emit final text.
            make_text_response("The GC content is 50%."),
        ]
    )

    stub = StubPlanner()
    result = await run_agent(
        "GC content of ATGCATGC",
        project_id="default-project",
        llm=llm,
        planner=stub,
        enable_critic=False,
        skip_approval_gate=True,
    )

    # The injected planner produced the plan exactly once (trivial plans don't replan).
    assert stub.calls == 1
    assert result.status == "completed"
    assert "50%" in result.response_text


# --- Sanity: existing make_plan unit tests still pass with the wrapper -------------
#
# The 5+ tests in test_planner.py exercise the underlying `make_plan()` function
# unchanged. We deliberately did NOT change planner.py; LocalPlanner is a thin
# wrapper around it. Those tests remain the source of truth for the LLM call
# itself.
