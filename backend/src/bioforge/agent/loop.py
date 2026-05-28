"""The agent loop: plan → (approval gate) → execute → critique → (replan once) → respond.

Shape:

    PLANNER:  Claude is forced to submit a structured `Plan` for the goal.
    APPROVAL: If the plan contains any `expensive` or `destructive` tool steps, pause and
              return a `pending_approval` result. The API persists the plan and surfaces
              an approval card; resumption goes through `resume_agent`.
    EXECUTOR: Manual tool-use loop. Sees the plan as context but is free to deviate.
              Produces a draft response.
    CRITIC:   Claude is forced to submit a `CriticVerdict` on the draft.
    REPLAN:   On a failing verdict, ONE replanning attempt with the complaints fed back.

Single-tool / trivial goals short-circuit past the critic. The loop signature
`run_agent(goal, project_id, ...)` takes `tool_set` as an explicit argument so the
multi-agent split later is a routing layer on top, not a rewrite.

**Streaming**: every function that produces an `AgentStep` accepts an optional
`on_step: Callable[[AgentStep], Awaitable[None]]`. When provided, the callback fires once
per step as it lands. Default `None` preserves the synchronous behavior. The SSE
endpoint (`api/agent.py`) plugs a queue-backed callback in and drains it into a
`text/event-stream`.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from anthropic.types import Message
from pydantic import ValidationError

from bioforge.agent.approval import requires_approval
from bioforge.agent.context import get_current_db_session
from bioforge.agent.critic import CriticVerdict
from bioforge.agent.grounding import ValidationReport, ground_response, judge_claims
from bioforge.agent.llm import LLM, UsageSummary, summarize_usage
from bioforge.agent.local_critic import LocalCritic
from bioforge.agent.local_executor import LocalExecutor
from bioforge.agent.local_planner import LocalPlanner
from bioforge.agent.memory import load_relevant_memory
from bioforge.agent.planner import Plan
from bioforge.agent.roles import (
    Critic,
    CriticContext,
    Executor,
    ExecutorContext,
    PlanContext,
    Planner,
)
from bioforge.config import settings
from bioforge.observability.tracing import (
    record_exception,
    set_agent_run_attrs,
    set_status_ok,
)
from bioforge.tools.base import ToolError
from bioforge.tools.registry import REGISTRY, execute_tool, list_tools, to_anthropic_tools

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_system_prompt() -> str:
    return (_PROMPTS_DIR / "system.md").read_text(encoding="utf-8")


StepType = Literal[
    "plan",
    "replan",
    "approval_requested",
    "approval_decision",
    "llm_call",
    "tool_call",
    "tool_error",
    "refusal",
    "critique",
    "validation",
    "final",
]

AgentStatus = Literal[
    "completed",
    "completed_after_replan",
    "critique_failed",
    "refused",
    "error",
    "iteration_cap",
    "pending_approval",
    "cancelled",
]


@dataclass
class AgentStep:
    idx: int
    type: StepType
    duration_ms: int
    # llm_call
    stop_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cache_read_tokens: int | None = None
    # tool_call / tool_error
    tool_name: str | None = None
    tool_input: dict | None = None
    tool_output: dict | None = None
    error: str | None = None
    # plan / replan / critique
    plan: dict | None = None
    verdict: dict | None = None
    # approval
    approval_reasons: list[str] | None = None
    approved: bool | None = None


@dataclass
class AgentResult:
    goal: str
    project_id: str
    response_text: str
    steps: list[AgentStep] = field(default_factory=list)
    usage: UsageSummary | None = None
    status: AgentStatus = "completed"
    model: str = ""
    pending_plan: dict | None = None
    approval_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "project_id": self.project_id,
            "response_text": self.response_text,
            "status": self.status,
            "model": self.model,
            "steps": [asdict(s) for s in self.steps],
            "usage": asdict(self.usage) if self.usage else None,
            "pending_plan": self.pending_plan,
            "approval_reasons": self.approval_reasons,
        }


# Type alias for the per-step streaming callback. Made permissive (Any) on input so tests
# can pass simple async lambdas / queue.put references without ceremony.
OnStep = Callable[[AgentStep], Awaitable[None]] | None


async def _emit(on_step: OnStep, step: AgentStep) -> None:
    """Fire the streaming callback if one was provided. Swallows callback errors so a
    crashed consumer (e.g. closed SSE connection) never aborts the agent run."""
    if on_step is None:
        return
    try:
        await on_step(step)
    except Exception:  # noqa: BLE001 — intentional, see docstring
        pass


# --- Grounding validator (BioForge v4 §4 Layer 3) — shadow-mode wiring ----------------
#
# Statuses where the run produced a real, user-facing response worth grounding. Error /
# refused / iteration_cap / pending_approval / cancelled carry no findings to check.
_GROUNDABLE_STATUSES: frozenset[str] = frozenset({"completed", "completed_after_replan", "critique_failed"})


def _tool_outputs_from_steps(steps: list[AgentStep]) -> list[dict]:
    """Pull the structured tool-result dicts out of a step list — the Layer-3 inventory source."""
    return [s.tool_output for s in steps if s.type == "tool_call" and s.tool_output is not None]


# Inline marker left where an unsupported numeric claim used to be. Deliberately digit-free
# so it carries no new number and survives re-validation cleanly.
_REDACTION_MARKER = "[unverifiable]"


def _enforce(text: str, report: ValidationReport) -> str:
    """Redact unsupported numeric claims in place and append an audit note (v4 §4 L5).

    Numeric claims have exact offsets, so they are spliced out (rightmost-first, to keep
    offsets valid) and replaced with a marker. Entity/mechanistic claims from the judge
    have no reliable offsets, so they are flagged in the footer rather than redacted in
    place. Either way the user sees what was removed/flagged and why — visible redaction
    (policy a): never a silent rewrite, never a vague qualitative substitution.
    """
    if report.ok:
        return text
    redacted = text
    for claim in sorted(report.unsupported, key=lambda c: c.start, reverse=True):
        redacted = redacted[: claim.start] + _REDACTION_MARKER + redacted[claim.end :]
    notes = [
        f'  - "{c.text}" (numeric) was removed: not traceable to a tool result in this run.'
        for c in sorted(report.unsupported, key=lambda c: c.start)
    ]
    notes += [
        f'  - "{jc.text}" ({jc.kind}) is not supported by any tool result in this run.'
        for jc in report.unsupported_judged
    ]
    if not notes:
        return redacted
    footer = (
        "\n\n---\n"
        f"[BioForge grounding] {len(notes)} claim(s) could not be traced to a tool result this run:\n"
        + "\n".join(notes)
    )
    return redacted + footer


async def _apply_grounding(
    *, response_text: str, steps: list[AgentStep], status: str, step_idx: int, llm: LLM, model: str
) -> tuple[str, AgentStep | None, UsageSummary]:
    """Run the grounding validator over a final response (BioForge v4 §4 Layers 3-4).

    Returns `(final_text, validation_step, judge_usage)`. The report rides in the existing
    `verdict` field so the `AgentStep` dataclass is not mutated (which would change every
    step's serialization).

    - disabled or no real response → `(response_text, None, zero)`, a true no-op;
    - numeric grounding (L3) always runs when enabled — deterministic, free;
    - the L4 entity/mechanistic judge runs only when `grounding_judge_enabled` (an extra
      model call); a judge failure is swallowed so the run still completes on numeric-only;
    - `grounding_mode="enforce"` redacts/flags unsupported claims; `"shadow"` records only.

    Default-off keeps the loop behaviorally identical until the flag is flipped.
    """
    zero = UsageSummary.zero(model)
    if not settings.grounding_enabled or status not in _GROUNDABLE_STATUSES:
        return response_text, None, zero
    t0 = time.monotonic()
    tool_outputs = _tool_outputs_from_steps(steps)
    report = ground_response(response_text, tool_outputs)
    judge_usage = zero
    if settings.grounding_judge_enabled:
        try:
            jr = await judge_claims(
                response_text=response_text,
                tool_outputs=tool_outputs,
                llm=llm,
                model=settings.grounding_judge_model or model,
            )
            report.judged_claims = jr.claims
            judge_usage = jr.usage
        except Exception as e:  # noqa: BLE001 — a judge failure must never break the run
            report.summary += f" | L4 judge skipped: {type(e).__name__}"
    # Recompute the gate over both layers.
    report.ok = report.ok and not report.unsupported_judged
    enforced = settings.grounding_mode == "enforce" and not report.ok
    final_text = _enforce(response_text, report) if enforced else response_text
    step = AgentStep(
        idx=step_idx,
        type="validation",
        duration_ms=int((time.monotonic() - t0) * 1000),
        verdict={**report.model_dump(), "enforced": enforced},
    )
    return final_text, step, judge_usage


def _build_system_prompt() -> list[dict]:
    return [
        {
            "type": "text",
            "text": _load_system_prompt(),
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _extract_text_blocks(message: Message) -> str:
    return "\n".join(b.text for b in message.content if b.type == "text").strip()


def _format_plan_for_executor(plan: Plan) -> str:
    if not plan.steps:
        return f"Plan summary: {plan.summary}"
    lines = [f"Plan summary: {plan.summary}", "", "Steps:"]
    for s in plan.steps:
        tool_part = f" [tool: {s.expected_tool}]" if s.expected_tool else ""
        lines.append(f"  {s.idx}. {s.description}{tool_part}")
        lines.append(f"     rationale: {s.rationale}")
    return "\n".join(lines)


def _build_executor_user_message(goal: str, plan: Plan | None, complaints: list[str] | None = None) -> str:
    parts = [f"Goal: {goal}"]
    if plan is not None and not plan.is_trivial:
        parts.append("")
        parts.append(_format_plan_for_executor(plan))
        parts.append("")
        parts.append(
            "Execute the plan by calling the appropriate tools. You may deviate from "
            "the plan if a tool returns unexpected output or fails — describe what you "
            "did and why in your final response."
        )
    if complaints:
        parts.append("")
        parts.append("A previous attempt at this goal was judged incomplete. Specific issues:")
        for c in complaints:
            parts.append(f"  - {c}")
        parts.append("")
        parts.append("Address each issue in this attempt.")
    return "\n".join(parts)


async def _execute(
    *,
    goal: str,
    plan: Plan | None,
    complaints: list[str] | None,
    llm: LLM,
    model: str,
    tool_tags: list[str] | None,
    max_iterations: int,
    step_idx_start: int,
    on_step: OnStep = None,
) -> tuple[str, list[AgentStep], UsageSummary, str]:
    """Run the executor (manual tool-use loop). Returns
    `(draft_response, steps_emitted, usage, terminal_status)`. Every step is also pushed
    through `on_step` as it lands."""
    tools = to_anthropic_tools(tags=tool_tags)
    system = _build_system_prompt()
    messages: list[dict] = [{"role": "user", "content": _build_executor_user_message(goal, plan, complaints)}]
    steps: list[AgentStep] = []
    total_usage = UsageSummary.zero(model)
    step_idx = step_idx_start

    async def _append(step: AgentStep) -> None:
        steps.append(step)
        await _emit(on_step, step)

    for _ in range(max_iterations):
        t0 = time.monotonic()
        try:
            response = await llm.complete(
                model=model,
                system=system,
                messages=messages,
                tools=tools if tools else None,
                max_tokens=4096,
            )
        except Exception as e:
            await _append(
                AgentStep(
                    idx=step_idx,
                    type="tool_error",
                    duration_ms=int((time.monotonic() - t0) * 1000),
                    error=f"LLM call failed: {type(e).__name__}: {e}",
                )
            )
            return (f"Executor error: {type(e).__name__}: {e}", steps, total_usage, "error")

        usage = summarize_usage(model, response)
        total_usage = total_usage.merge(usage)
        await _append(
            AgentStep(
                idx=step_idx,
                type="llm_call",
                duration_ms=int((time.monotonic() - t0) * 1000),
                stop_reason=response.stop_reason,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_creation_tokens=usage.cache_creation_tokens,
                cache_read_tokens=usage.cache_read_tokens,
            )
        )
        step_idx += 1

        if response.stop_reason == "end_turn":
            text = _extract_text_blocks(response)
            await _append(AgentStep(idx=step_idx, type="final", duration_ms=0))
            return (text, steps, total_usage, "completed")

        if response.stop_reason == "refusal":
            text = _extract_text_blocks(response) or "(model refused the request)"
            await _append(AgentStep(idx=step_idx, type="refusal", duration_ms=0))
            return (text, steps, total_usage, "refused")

        if response.stop_reason == "tool_use":
            assistant_content = [b.model_dump() for b in response.content]
            messages.append({"role": "assistant", "content": assistant_content})

            tool_results: list[dict] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                t_tool = time.monotonic()
                tool_name = block.name
                tool_input = block.input if isinstance(block.input, dict) else {}
                if tool_name not in REGISTRY:
                    err = f"Tool {tool_name!r} is not registered. Available: {sorted(REGISTRY)}"
                    await _append(
                        AgentStep(
                            idx=step_idx,
                            type="tool_error",
                            duration_ms=int((time.monotonic() - t_tool) * 1000),
                            tool_name=tool_name,
                            tool_input=tool_input,
                            error=err,
                        )
                    )
                    step_idx += 1
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": err,
                            "is_error": True,
                        }
                    )
                    continue

                try:
                    output = await execute_tool(tool_name, tool_input)
                    output_dict = output.model_dump()
                    await _append(
                        AgentStep(
                            idx=step_idx,
                            type="tool_call",
                            duration_ms=int((time.monotonic() - t_tool) * 1000),
                            tool_name=tool_name,
                            tool_input=tool_input,
                            tool_output=output_dict,
                        )
                    )
                    step_idx += 1
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(output_dict),
                        }
                    )
                except (ToolError, ValidationError, ValueError) as e:
                    err = f"{type(e).__name__}: {e}"
                    await _append(
                        AgentStep(
                            idx=step_idx,
                            type="tool_error",
                            duration_ms=int((time.monotonic() - t_tool) * 1000),
                            tool_name=tool_name,
                            tool_input=tool_input,
                            error=err,
                        )
                    )
                    step_idx += 1
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": err,
                            "is_error": True,
                        }
                    )

            messages.append({"role": "user", "content": tool_results})
            continue

        text = _extract_text_blocks(response)
        return (
            text or f"Executor stopped: stop_reason={response.stop_reason}",
            steps,
            total_usage,
            "error",
        )

    return (
        f"Executor reached the iteration cap ({max_iterations}). Partial trace recorded.",
        steps,
        total_usage,
        "iteration_cap",
    )


async def _try_plan(
    goal: str,
    *,
    planner: Planner,
    model: str,
    available_tools_for_planner: list,
    step_idx: int,
    is_replan: bool,
    previous_complaints: list[str] | None = None,
    on_step: OnStep = None,
    memory_context: str = "",
) -> tuple[Plan | None, AgentStep, UsageSummary]:
    """Dispatch a plan request through a Planner role and wrap the result in an AgentStep.

    Phase 5.1: the planner is now an injectable Protocol implementation instead
    of a direct call to `make_plan()`. The complaints-formatting logic moved
    into LocalPlanner.make_plan (and any future Planner impl) — this function
    is now purely the loop-level concerns: timing, error wrapping, step emission,
    usage extraction. Existing callers get LocalPlanner by default via
    `run_agent`, so behavior is unchanged.
    """
    t0 = time.monotonic()
    ctx = PlanContext(
        goal=goal,
        model=model,
        available_tools=available_tools_for_planner,
        memory_context=memory_context,
        previous_complaints=list(previous_complaints or []),
    )
    try:
        plan = await planner.make_plan(ctx)
    except Exception as e:
        step = AgentStep(
            idx=step_idx,
            type="replan" if is_replan else "plan",
            duration_ms=int((time.monotonic() - t0) * 1000),
            error=f"Planner failed: {type(e).__name__}: {e}",
        )
        await _emit(on_step, step)
        return (None, step, UsageSummary.zero(model))

    # Usage is exposed by the planner role via the `last_usage` side-channel
    # (see LocalPlanner). Protocols can omit the attribute; in that case we
    # report zero rather than crash — the run still completes.
    usage = getattr(planner, "last_usage", None) or UsageSummary.zero(model)
    step = AgentStep(
        idx=step_idx,
        type="replan" if is_replan else "plan",
        duration_ms=int((time.monotonic() - t0) * 1000),
        plan=plan.model_dump(),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_creation_tokens=usage.cache_creation_tokens,
        cache_read_tokens=usage.cache_read_tokens,
    )
    await _emit(on_step, step)
    return (plan, step, usage)


async def _try_critique(
    *,
    critic: Critic,
    goal: str,
    plan: Plan | None,
    exec_steps: list[AgentStep],
    draft_response: str,
    model: str,
    step_idx: int,
    on_step: OnStep = None,
) -> tuple[CriticVerdict | None, AgentStep, UsageSummary]:
    """Dispatch a critique through a Critic role and wrap the result in an AgentStep.

    Phase 5.3: the LLM call moved into LocalCritic; this function is now purely
    the loop-level concerns (timing, error wrapping, step emission, usage
    extraction). Existing callers get LocalCritic by default via run_agent.
    """
    t0 = time.monotonic()
    ctx = CriticContext(
        goal=goal,
        plan=plan,
        response_text=draft_response,
        exec_steps=exec_steps,
        model=model,
        step_idx=step_idx,
        on_step=on_step,
    )
    try:
        verdict = await critic.critique(ctx)
    except Exception as e:
        step = AgentStep(
            idx=step_idx,
            type="critique",
            duration_ms=int((time.monotonic() - t0) * 1000),
            error=f"Critic failed: {type(e).__name__}: {e}",
        )
        await _emit(on_step, step)
        return (None, step, UsageSummary.zero(model))

    usage = getattr(critic, "last_usage", None) or UsageSummary.zero(model)
    step = AgentStep(
        idx=step_idx,
        type="critique",
        duration_ms=int((time.monotonic() - t0) * 1000),
        verdict=verdict.model_dump(),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_creation_tokens=usage.cache_creation_tokens,
        cache_read_tokens=usage.cache_read_tokens,
    )
    await _emit(on_step, step)
    return (verdict, step, usage)


async def _execute_via_role(
    *,
    executor: Executor,
    goal: str,
    plan: Plan | None,
    complaints: list[str] | None,
    model: str,
    tool_tags: list[str] | None,
    max_iterations: int,
    step_idx_start: int,
    on_step: OnStep = None,
) -> tuple[str, list[AgentStep], UsageSummary, str]:
    """Dispatch an execute request through an Executor role and unpack the
    (draft, steps, usage, status) tuple the loop expects.

    The Executor Protocol returns ExecutionResult (Protocol-friendly flat
    shape). The in-process role attaches the raw AgentStep list and the
    terminal status string as side-channel attributes — we read them here
    so the rest of the loop's control flow stays unchanged. Phase 5.2."""
    ctx = ExecutorContext(
        plan=plan,
        goal=goal,
        model=model,
        complaints=list(complaints or []),
        tool_tags=tool_tags,
        max_iterations=max_iterations,
        step_idx_start=step_idx_start,
        on_step=on_step,
    )
    result = await executor.execute(ctx)
    steps = list(getattr(executor, "last_steps", []) or [])
    usage = getattr(executor, "last_usage", None) or UsageSummary.zero(model)
    status = getattr(executor, "last_status", "") or (
        "completed"
        if not result.refused and not result.finished_with_tool_use
        else "refused"
        if result.refused
        else "iteration_cap"
    )
    return (result.response_text, steps, usage, status)


async def _execute_critique_replan(
    *,
    goal: str,
    plan: Plan | None,
    project_id: str,
    llm: LLM,
    model: str,
    tool_tags: list[str] | None,
    max_iterations: int,
    enable_critic: bool,
    step_idx_start: int,
    on_step: OnStep = None,
    memory_context: str = "",
    planner: Planner | None = None,
    executor: Executor | None = None,
    critic: Critic | None = None,
) -> AgentResult:
    """The portion of the loop after the plan is approved. Shared by `run_agent` (post
    approval-gate when no approval is needed) and `resume_agent` (when approval was given).
    """
    all_steps: list[AgentStep] = []
    total_usage = UsageSummary.zero(model)
    step_idx = step_idx_start

    available_tools = list_tools(tags=tool_tags)

    # Default role instances (Phase 5.1-5.3). Each role is per-run state-bearing
    # (last_usage etc.) so we construct fresh instances when the caller didn't
    # inject custom ones.
    planner = planner if planner is not None else LocalPlanner(llm)
    executor = executor if executor is not None else LocalExecutor(llm)
    critic = critic if critic is not None else LocalCritic(llm)

    # --- EXECUTE (attempt 1) ---
    draft, exec_steps, exec_usage, exec_status = await _execute_via_role(
        executor=executor,
        goal=goal,
        plan=plan,
        complaints=None,
        model=model,
        tool_tags=tool_tags,
        max_iterations=max_iterations,
        step_idx_start=step_idx,
        on_step=on_step,
    )
    all_steps.extend(exec_steps)
    step_idx += len(exec_steps)
    total_usage = total_usage.merge(exec_usage)

    if exec_status in ("error", "iteration_cap", "refused"):
        return AgentResult(
            goal=goal,
            project_id=project_id,
            response_text=draft,
            steps=all_steps,
            usage=total_usage,
            status=exec_status,  # type: ignore[arg-type]
            model=model,
        )

    # --- CRITIQUE (skipped for trivial plans or when disabled) ---
    if not enable_critic or (plan is not None and plan.is_trivial):
        return AgentResult(
            goal=goal,
            project_id=project_id,
            response_text=draft,
            steps=all_steps,
            usage=total_usage,
            status="completed",
            model=model,
        )

    verdict, critique_step, critique_usage = await _try_critique(
        critic=critic,
        goal=goal,
        plan=plan,
        exec_steps=exec_steps,
        draft_response=draft,
        model=model,
        step_idx=step_idx,
        on_step=on_step,
    )
    all_steps.append(critique_step)
    step_idx += 1
    total_usage = total_usage.merge(critique_usage)

    if verdict is None or verdict.satisfies_goal:
        return AgentResult(
            goal=goal,
            project_id=project_id,
            response_text=draft,
            steps=all_steps,
            usage=total_usage,
            status="completed",
            model=model,
        )

    # --- REPLAN (one attempt) ---
    replan, replan_step, replan_usage = await _try_plan(
        goal,
        planner=planner,
        model=model,
        available_tools_for_planner=available_tools,
        step_idx=step_idx,
        is_replan=True,
        previous_complaints=verdict.concrete_complaints or [verdict.reason],
        on_step=on_step,
        memory_context=memory_context,
    )
    all_steps.append(replan_step)
    step_idx += 1
    total_usage = total_usage.merge(replan_usage)

    draft2, exec_steps2, exec_usage2, exec_status2 = await _execute_via_role(
        executor=executor,
        goal=goal,
        plan=replan,
        complaints=verdict.concrete_complaints or [verdict.reason],
        model=model,
        tool_tags=tool_tags,
        max_iterations=max_iterations,
        step_idx_start=step_idx,
        on_step=on_step,
    )
    all_steps.extend(exec_steps2)
    step_idx += len(exec_steps2)
    total_usage = total_usage.merge(exec_usage2)

    if exec_status2 in ("error", "iteration_cap", "refused"):
        return AgentResult(
            goal=goal,
            project_id=project_id,
            response_text=draft2,
            steps=all_steps,
            usage=total_usage,
            status=exec_status2,  # type: ignore[arg-type]
            model=model,
        )

    verdict2, critique_step2, critique_usage2 = await _try_critique(
        critic=critic,
        goal=goal,
        plan=replan,
        exec_steps=exec_steps2,
        draft_response=draft2,
        model=model,
        step_idx=step_idx,
        on_step=on_step,
    )
    all_steps.append(critique_step2)
    total_usage = total_usage.merge(critique_usage2)

    if verdict2 is None or verdict2.satisfies_goal:
        return AgentResult(
            goal=goal,
            project_id=project_id,
            response_text=draft2,
            steps=all_steps,
            usage=total_usage,
            status="completed_after_replan",
            model=model,
        )

    return AgentResult(
        goal=goal,
        project_id=project_id,
        response_text=(
            f"{draft2}\n\n---\n\n"
            "Note: I attempted this goal twice but the critic judged neither attempt "
            f"satisfactory. Remaining concerns: {'; '.join(verdict2.concrete_complaints) or verdict2.reason}"
        ),
        steps=all_steps,
        usage=total_usage,
        status="critique_failed",
        model=model,
    )


async def run_agent(
    goal: str,
    *,
    project_id: str,
    llm: LLM | None = None,
    model: str | None = None,
    tool_tags: list[str] | None = None,
    max_iterations: int | None = None,
    enable_critic: bool = True,
    skip_approval_gate: bool = False,
    on_step: OnStep = None,
    planner: Planner | None = None,
    executor: Executor | None = None,
    critic: Critic | None = None,
) -> AgentResult:
    """Run the full plan → (approval) → execute → critique → (replan once) loop.

    `skip_approval_gate=True` is for tests/CLI tools that want to bypass the pause-for-
    approval semantics. The API path always leaves it `False`.

    `on_step` is an async callback fired once per `AgentStep` as it lands. Use it for
    SSE streaming. Default `None` is the non-streaming path used by the JSON endpoint
    and the unit-test paths that examine `result.steps` after completion.

    Phase 5.1-5.3 role injection: `planner`, `executor`, `critic` are Protocol
    implementations the loop dispatches through. Each defaults to its `Local*`
    in-process implementation, which matches Phase 0-4 behavior byte-for-byte.
    Passing custom roles lets tests + future remote sub-agents plug in without
    touching the loop.
    """
    model = model or settings.default_model
    max_iterations = max_iterations or settings.max_agent_iterations
    llm = llm or LLM()
    if planner is None:
        planner = LocalPlanner(llm)
    if executor is None:
        executor = LocalExecutor(llm)
    if critic is None:
        critic = LocalCritic(llm)

    # Import the tracer LAZILY here so tests that install a TracerProvider after this
    # module's import time see real spans. A module-level `tracer = ...` would freeze
    # to whatever was current at import.
    from opentelemetry import trace as _otel_trace

    _tracer = _otel_trace.get_tracer("bioforge.agent")

    with _tracer.start_as_current_span("agent.run") as root_span:
        set_agent_run_attrs(root_span, goal=goal, project_id=project_id, model=model)

        all_steps: list[AgentStep] = []
        total_usage = UsageSummary.zero(model)
        step_idx = 0

        available_tools = list_tools(tags=tool_tags)

        # --- MEMORY (loaded once at start; re-used across replans) ---
        memory_context = ""
        db_session = get_current_db_session()
        if db_session is not None and project_id:
            with _tracer.start_as_current_span("agent.load_memory") as mem_span:
                try:
                    memory_context = await load_relevant_memory(db_session, project_id, goal)
                    mem_span.set_attribute("bioforge.memory_context_chars", len(memory_context))
                except Exception as e:  # noqa: BLE001
                    record_exception(mem_span, e)
                    memory_context = ""

        # --- PLAN ---
        with _tracer.start_as_current_span("agent.plan") as plan_span:
            plan, plan_step, plan_usage = await _try_plan(
                goal,
                planner=planner,
                model=model,
                available_tools_for_planner=available_tools,
                step_idx=step_idx,
                is_replan=False,
                on_step=on_step,
                memory_context=memory_context,
            )
            if plan is not None:
                plan_span.set_attribute("bioforge.plan_size", len(plan.steps))
                plan_span.set_attribute("bioforge.plan_is_trivial", plan.is_trivial)
        all_steps.append(plan_step)
        step_idx += 1
        if plan_usage.input_tokens or plan_usage.output_tokens:
            total_usage = total_usage.merge(plan_usage)

        if plan is not None and plan.is_trivial and not plan.steps:
            refusal_step = AgentStep(idx=step_idx, type="refusal", duration_ms=0)
            all_steps.append(refusal_step)
            await _emit(on_step, refusal_step)
            root_span.set_attribute("bioforge.status", "refused")
            set_status_ok(root_span)
            return AgentResult(
                goal=goal,
                project_id=project_id,
                response_text=plan.summary,
                steps=all_steps,
                usage=total_usage,
                status="refused",
                model=model,
            )

        # --- APPROVAL GATE ---
        if plan is not None and not skip_approval_gate:
            with _tracer.start_as_current_span("agent.approval_gate") as approval_span:
                requirement = requires_approval(plan, REGISTRY)
                approval_span.set_attribute("bioforge.approval_required", requirement.required)
                approval_span.set_attribute("bioforge.approval_reasons_count", len(requirement.reasons))
                if requirement.required:
                    approval_step = AgentStep(
                        idx=step_idx,
                        type="approval_requested",
                        duration_ms=0,
                        approval_reasons=requirement.reasons,
                    )
                    all_steps.append(approval_step)
                    await _emit(on_step, approval_step)
                    root_span.set_attribute("bioforge.status", "pending_approval")
                    set_status_ok(root_span)
                    return AgentResult(
                        goal=goal,
                        project_id=project_id,
                        response_text=(
                            "Approval required before running this plan. Reasons:\n"
                            + "\n".join(f"  - {r}" for r in requirement.reasons)
                        ),
                        steps=all_steps,
                        usage=total_usage,
                        status="pending_approval",
                        model=model,
                        pending_plan=plan.model_dump(),
                        approval_reasons=requirement.reasons,
                    )

        # --- EXECUTE → CRITIQUE → (REPLAN) ---
        tail = await _execute_critique_replan(
            goal=goal,
            plan=plan,
            project_id=project_id,
            llm=llm,
            model=model,
            tool_tags=tool_tags,
            max_iterations=max_iterations,
            enable_critic=enable_critic,
            step_idx_start=step_idx,
            on_step=on_step,
            memory_context=memory_context,
            planner=planner,
            executor=executor,
            critic=critic,
        )
        all_steps.extend(tail.steps)
        total_usage = total_usage.merge(tail.usage or UsageSummary.zero(model))
        final_text, grounding_step, grounding_usage = await _apply_grounding(
            response_text=tail.response_text,
            steps=all_steps,
            status=tail.status,
            step_idx=len(all_steps),
            llm=llm,
            model=model,
        )
        if grounding_step is not None:
            all_steps.append(grounding_step)
            total_usage = total_usage.merge(grounding_usage)
            await _emit(on_step, grounding_step)
        root_span.set_attribute("bioforge.status", tail.status)
        root_span.set_attribute("bioforge.steps_total", len(all_steps))
        if total_usage.cost_usd:
            root_span.set_attribute("bioforge.cost_usd", total_usage.cost_usd)
        set_status_ok(root_span)
        return AgentResult(
            goal=goal,
            project_id=project_id,
            response_text=final_text,
            steps=all_steps,
            usage=total_usage,
            status=tail.status,
            model=model,
        )


async def resume_agent(
    *,
    goal: str,
    plan: Plan,
    project_id: str,
    step_idx_start: int,
    llm: LLM | None = None,
    model: str | None = None,
    tool_tags: list[str] | None = None,
    max_iterations: int | None = None,
    enable_critic: bool = True,
    on_step: OnStep = None,
    planner: Planner | None = None,
    executor: Executor | None = None,
    critic: Critic | None = None,
) -> AgentResult:
    """Resume an agent run after approval was granted. The caller has fetched the
    pending trace, validated the persisted plan, and now wants the executor + critic
    portion to run with the same goal + plan.

    Emits ONLY the new steps (executor, critic, replan/exec/critique-2 if needed). The
    caller is responsible for merging these into the existing trace's step list.
    """
    model = model or settings.default_model
    max_iterations = max_iterations or settings.max_agent_iterations
    llm = llm or LLM()

    # Memory for the replan inside execute_critique_replan, in case the critic fails.
    memory_context = ""
    db_session = get_current_db_session()
    if db_session is not None and project_id:
        try:
            memory_context = await load_relevant_memory(db_session, project_id, goal)
        except Exception:  # noqa: BLE001
            memory_context = ""

    result = await _execute_critique_replan(
        goal=goal,
        plan=plan,
        project_id=project_id,
        llm=llm,
        model=model,
        tool_tags=tool_tags,
        max_iterations=max_iterations,
        enable_critic=enable_critic,
        step_idx_start=step_idx_start,
        on_step=on_step,
        memory_context=memory_context,
        planner=planner,
        executor=executor,
        critic=critic,
    )
    final_text, grounding_step, grounding_usage = await _apply_grounding(
        response_text=result.response_text,
        steps=result.steps,
        status=result.status,
        step_idx=len(result.steps),
        llm=llm,
        model=model,
    )
    if grounding_step is not None:
        result.steps.append(grounding_step)
        if result.usage is not None:
            result.usage = result.usage.merge(grounding_usage)
        await _emit(on_step, grounding_step)
    result.response_text = final_text
    return result


def run_agent_sync(goal: str, *, project_id: str, **kwargs) -> AgentResult:
    return asyncio.run(run_agent(goal, project_id=project_id, **kwargs))
