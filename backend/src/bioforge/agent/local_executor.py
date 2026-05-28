"""Phase 5.2: LocalExecutor — in-process Executor role.

Wraps the existing `_execute()` function in `agent/loop.py` with an
Executor-Protocol-conforming class. The agent loop dispatches the
execute step through a role instance, so future replacements (a remote
sub-agent, a deterministic replayer, a smaller-cheaper model just for
tool selection) plug in without touching the loop.

Behavioral equivalence is the gate: with no `executor` argument, the
loop instantiates `LocalExecutor(llm)` and the trace is byte-for-byte the
same as the Phase 0-4 path.

# Side-channel attributes

The Executor Protocol returns `ExecutionResult` — a flat shape with
response text, tool-call list, iteration count, and refusal info. The
agent loop also needs the raw `AgentStep[]` list (for trace persistence
+ SSE streaming) and the terminal `status` string (to decide whether to
critique / replan). Those flow through three side-channel attributes set
on the role instance after each call:

  - `last_steps: list[AgentStep]` — the trace fragment this executor run produced.
  - `last_usage: UsageSummary | None` — token usage summed across LLM calls.
  - `last_status: str` — "completed" / "refused" / "error" / "iteration_cap".

The narrow Protocol contract (`execute(ctx) -> ExecutionResult`) stays
clean; the side-channels are an in-process implementation detail. A
remote executor would marshall steps + usage + status across its RPC
boundary using the same attribute names.

# Why a lazy import

`_execute()` lives in `agent/loop.py`, which itself imports `LocalExecutor`
to use as the default. To avoid a circular import at module-load time, we
defer the `_execute` import to the first call. The cycle is fine at
runtime — by the time `execute()` runs, both modules are fully loaded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bioforge.agent.llm import LLM, UsageSummary
from bioforge.agent.roles import ExecutionResult, ExecutorContext

if TYPE_CHECKING:
    pass


class LocalExecutor:
    """In-process Executor. Routes through `_execute()` and shapes the result."""

    def __init__(self, llm: LLM) -> None:
        self.llm = llm
        self.last_usage: UsageSummary | None = None
        self.last_steps: list[Any] = []  # list[AgentStep] at runtime
        self.last_status: str = ""

    async def execute(self, ctx: ExecutorContext) -> ExecutionResult:
        # Lazy import to avoid the loop.py ↔ local_executor.py cycle (loop.py
        # imports LocalExecutor at module top to use as the default executor;
        # we want _execute at call time once both modules are fully loaded).
        from bioforge.agent.loop import _execute

        draft, steps, usage, status = await _execute(
            goal=ctx.goal,
            plan=ctx.plan,
            complaints=list(ctx.complaints) or None,
            llm=self.llm,
            model=ctx.model,
            tool_tags=ctx.tool_tags,
            max_iterations=ctx.max_iterations,
            step_idx_start=ctx.step_idx_start,
            on_step=ctx.on_step,
        )
        self.last_steps = steps
        self.last_usage = usage
        self.last_status = status

        # Flatten the trace into the ExecutionResult.tool_calls shape: one
        # dict per tool_call / tool_error step. The Protocol consumer (a
        # future remote critic, for example) reads this rather than the raw
        # AgentStep list.
        tool_calls: list[dict[str, Any]] = []
        for s in steps:
            if s.type == "tool_call":
                tool_calls.append(
                    {
                        "name": s.tool_name,
                        "input": s.tool_input,
                        "output": s.tool_output,
                        "duration_ms": s.duration_ms,
                    }
                )
            elif s.type == "tool_error":
                tool_calls.append(
                    {
                        "name": s.tool_name,
                        "input": s.tool_input,
                        "error": s.error,
                        "duration_ms": s.duration_ms,
                    }
                )

        iterations = sum(1 for s in steps if s.type == "llm_call")
        return ExecutionResult(
            response_text=draft,
            tool_calls=tool_calls,
            iterations_used=iterations,
            finished_with_tool_use=(status == "iteration_cap"),
            refused=(status == "refused"),
            refusal_reason=draft if status == "refused" else "",
        )
