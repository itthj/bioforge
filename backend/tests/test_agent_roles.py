"""Tests that codify the Phase 5 role contracts in `agent.roles`.

The Phase 0-4 implementation runs planner/executor/critic as in-loop functions
rather than role objects. These tests verify:

  1. The Protocol shapes (Planner, Executor, Critic) are well-formed enough
     that a hand-written stub implementation satisfies `isinstance` checks.
  2. The dataclass contexts (PlanContext, ExecutorContext, CriticContext)
     accept the same parameters the existing loop already produces — so the
     Phase 5.1-5.3 refactor that wraps the in-loop functions in Protocol
     objects will be a mechanical change, not a redesign.
  3. ExecutionResult round-trips the trail of tool calls so the critic has
     enough context to judge whether the goal was satisfied.

These tests will become regression tests once the actual Planner/Executor/
Critic implementations land in Phase 5.1-5.3 — they pin the contract.
"""

from __future__ import annotations

from typing import Any

from bioforge.agent.critic import CriticVerdict
from bioforge.agent.planner import Plan, PlanStep
from bioforge.agent.roles import (
    Critic,
    CriticContext,
    ExecutionResult,
    Executor,
    ExecutorContext,
    PlanContext,
    Planner,
)

# --- PlanContext / ExecutorContext / CriticContext -------------------------------


def test_plan_context_accepts_all_fields_used_by_existing_planner() -> None:
    """The existing in-loop _try_plan reads: goal, model, available_tools,
    memory_context, project_id. All must be present on the new PlanContext."""
    ctx = PlanContext(
        goal="What is the GC content of ATGC?",
        model="claude-sonnet-4-6",
        available_tools=[{"name": "gc_content", "description": "...", "input_schema": {}}],
        memory_context="user preferences: ...",
        project_id="default-project",
    )
    assert ctx.goal.startswith("What is")
    assert ctx.model == "claude-sonnet-4-6"
    assert len(ctx.available_tools) == 1
    assert ctx.project_id == "default-project"
    # on_progress is optional and defaults to None.
    assert ctx.on_progress is None


def test_executor_context_accepts_plan_and_iteration_cap() -> None:
    plan = Plan(
        is_trivial=True,
        summary="Just call gc_content.",
        steps=[
            PlanStep(idx=0, description="run gc_content", expected_tool="gc_content", rationale="direct answer"),
        ],
    )
    ctx = ExecutorContext(
        plan=plan,
        goal="What is the GC content?",
        model="claude-sonnet-4-6",
        available_tools=[],
        max_iterations=15,
    )
    assert ctx.max_iterations == 15
    assert ctx.plan.is_trivial is True


def test_critic_context_takes_plan_response_and_tool_trail() -> None:
    plan = Plan(is_trivial=True, summary="...", steps=[])
    ctx = CriticContext(
        goal="...",
        plan=plan,
        response_text="GC content is 50%.",
        tool_calls_made=[
            {"name": "gc_content", "input": {"sequence": "ATGC"}, "output": {"gc": 0.5}},
        ],
    )
    assert len(ctx.tool_calls_made) == 1
    assert ctx.tool_calls_made[0]["name"] == "gc_content"


# --- ExecutionResult shape --------------------------------------------------------


def test_execution_result_holds_full_tool_trail() -> None:
    result = ExecutionResult(
        response_text="The reverse complement is GCAT.",
        tool_calls=[
            {"name": "reverse_complement", "input": {"sequence": "ATGC"}, "output": {"result": "GCAT"}},
        ],
        iterations_used=2,
        finished_with_tool_use=False,
        refused=False,
    )
    assert result.iterations_used == 2
    assert result.refused is False
    assert "GCAT" in result.tool_calls[0]["output"]["result"]


def test_execution_result_refusal_path() -> None:
    """When the agent refuses (capability gap, fabrication risk), the executor
    surfaces it explicitly rather than synthesizing a fake answer."""
    result = ExecutionResult(
        response_text="I cannot answer this — no tool covers it.",
        tool_calls=[],
        iterations_used=1,
        refused=True,
        refusal_reason="No tool maps to this capability.",
    )
    assert result.refused is True
    assert result.refusal_reason


# --- Protocol conformance: a minimal stub satisfies each role -----------------


class _StubPlanner:
    async def make_plan(self, ctx: PlanContext) -> Plan:
        return Plan(is_trivial=True, summary="stub", steps=[])


class _StubExecutor:
    async def execute(self, ctx: ExecutorContext) -> ExecutionResult:
        return ExecutionResult(response_text="ok", tool_calls=[], iterations_used=0)


class _StubCritic:
    async def critique(self, ctx: CriticContext) -> CriticVerdict:
        return CriticVerdict(satisfies_goal=True, reason="stub", concrete_complaints=[])


def test_minimal_stub_satisfies_planner_protocol() -> None:
    assert isinstance(_StubPlanner(), Planner)


def test_minimal_stub_satisfies_executor_protocol() -> None:
    assert isinstance(_StubExecutor(), Executor)


def test_minimal_stub_satisfies_critic_protocol() -> None:
    assert isinstance(_StubCritic(), Critic)


async def test_stub_executor_returns_execution_result() -> None:
    """End-to-end: a real Plan goes in via ExecutorContext, an
    ExecutionResult comes out. Verifies the dataclass round-trip without
    needing real LLM."""
    plan = Plan(is_trivial=True, summary="...", steps=[])
    ctx = ExecutorContext(plan=plan, goal="g", model="claude-sonnet-4-6", available_tools=[])

    def _accept(o: Any) -> bool:
        return isinstance(o, ExecutionResult)

    executor = _StubExecutor()
    result = await executor.execute(ctx)
    assert _accept(result)
