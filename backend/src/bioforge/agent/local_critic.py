"""Phase 5.3: LocalCritic — in-process Critic role.

Wraps `evaluate()` in `agent/critic.py` (the LLM-driven critic that emits a
forced-tool-use `submit_verdict`) with a Critic-Protocol-conforming class.
The loop's `_try_critique` becomes the wrapper that handles timing / error
conversion / step emission; the role itself is just "given context, return
a CriticVerdict."

# Side-channel for usage

Same pattern as LocalPlanner / LocalExecutor: `last_usage` is set on the
instance after each successful call so the loop can merge tokens into the
run's total without widening the Protocol.

# Errors propagate

Just like LocalPlanner. The role does NOT swallow exceptions — the loop's
`_try_critique` is responsible for converting failures into a typed
AgentStep with the error message. Keeping the boundary clean means a
different Critic implementation (remote, replay-from-trace, deterministic
heuristic) drops in without re-implementing error handling.
"""

from __future__ import annotations

from bioforge.agent.critic import CriticVerdict
from bioforge.agent.critic import evaluate as _evaluate_fn
from bioforge.agent.llm import LLM, UsageSummary
from bioforge.agent.roles import CriticContext


class LocalCritic:
    """In-process Critic. Wraps `evaluate()` with the role API."""

    def __init__(self, llm: LLM) -> None:
        self.llm = llm
        self.last_usage: UsageSummary | None = None
        self.last_raw_input: dict | None = None

    async def critique(self, ctx: CriticContext) -> CriticVerdict:
        """Produce a verdict on `ctx.response_text` against `ctx.goal`.

        Reads `ctx.exec_steps` (the raw AgentStep list) rather than the
        flattened `tool_calls_made` view because `evaluate()` builds the
        critic prompt from the full step trail (it sees tool inputs,
        outputs, and errors as the executor saw them).
        """
        if ctx.on_progress is not None:
            await ctx.on_progress("Critiquing the draft response...")

        result = await _evaluate_fn(
            goal=ctx.goal,
            plan=ctx.plan,
            steps=ctx.exec_steps,
            draft_response=ctx.response_text,
            llm=self.llm,
            model=ctx.model,
        )
        self.last_usage = result.usage
        self.last_raw_input = result.raw_input
        return result.verdict
