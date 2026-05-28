"""Phase 5.1: LocalPlanner — in-process Planner role.

This is the FIRST role implementation in the Phase 5 split. It wraps the
existing `make_plan()` function (which Phase 0-4 called directly from the
agent loop) into a Planner-Protocol-conforming object. The agent loop now
dispatches plan calls through a Planner instance, so future implementations
(remote sub-agent, smaller-cheaper-faster model, replay-from-trace) can swap
in via dependency injection without touching the loop.

Behavioral equivalence with the previous in-loop call is the gate for this
slice: the same goal + complaints inputs produce the same Plan output. The
existing 23-test suite for `make_plan()` exercises that underlying function
unchanged; new tests here exercise the wrapper + the loop dispatch.

# Usage information

The Planner Protocol declares `make_plan(ctx) -> Plan`. The agent loop also
needs token usage so it can sum cost across roles. We expose that via the
`last_usage` attribute (populated after each call) rather than widening the
Protocol — keeps the role contract narrow ("produce a Plan") and lets the
loop pick up the side-channel data when needed. A remote planner sub-agent
in a later phase would expose the same attribute over its RPC boundary.
"""

from __future__ import annotations

from bioforge.agent.llm import LLM, UsageSummary
from bioforge.agent.planner import Plan
from bioforge.agent.planner import make_plan as _make_plan_fn
from bioforge.agent.roles import PlanContext


class LocalPlanner:
    """In-process Planner. Wraps `make_plan()` with the role API.

    The LLM is held as instance state so multiple plan calls (initial + replan)
    share the same client. `last_usage` is updated on every successful call;
    the agent loop reads it to merge planner tokens into the run's total.

    On planner failure (LLM error, invalid plan payload) the exception
    propagates — the agent loop's `_try_plan` wrapper catches and converts to
    a typed AgentStep with the error message. Role-internal try/except would
    swallow the source location and make debugging harder.
    """

    def __init__(self, llm: LLM) -> None:
        self.llm = llm
        self.last_usage: UsageSummary | None = None
        self.last_raw_input: dict | None = None

    async def make_plan(self, ctx: PlanContext) -> Plan:
        """Produce a Plan for the goal in `ctx`.

        If `ctx.previous_complaints` is non-empty, prepends them to the goal
        so the planner LLM sees what failed last time and can produce a
        revised plan that addresses each concern. This matches the prompt
        shape the Phase 0-4 loop used.
        """
        planner_goal = ctx.goal
        if ctx.previous_complaints:
            planner_goal = (
                f"{ctx.goal}\n\nThe previous attempt failed because:\n"
                + "\n".join(f"  - {c}" for c in ctx.previous_complaints)
                + "\n\nProduce a revised plan that addresses each issue."
            )

        # If the caller registered an on_progress callback, surface a short
        # heartbeat so streaming clients know the planner is alive. Optional;
        # the existing loop didn't emit one because the planner returns in
        # a single LLM round-trip.
        if ctx.on_progress is not None:
            await ctx.on_progress(
                "Planning a route through available tools..."
                if not ctx.previous_complaints
                else "Re-planning to address critic complaints..."
            )

        result = await _make_plan_fn(
            planner_goal,
            llm=self.llm,
            model=ctx.model,
            available_tools=ctx.available_tools,
            memory_context=ctx.memory_context,
        )
        self.last_usage = result.usage
        self.last_raw_input = result.raw_input
        return result.plan
