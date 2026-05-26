"""Critic: evaluates whether the executor's draft response satisfies the goal.

Uses forced tool-use (`submit_verdict`) for structured output, same pattern as the planner.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, ValidationError

from bioforge.agent.llm import LLM, UsageSummary, summarize_usage
from bioforge.agent.planner import Plan

if TYPE_CHECKING:
    from bioforge.agent.loop import AgentStep

_PROMPTS_DIR = Path(__file__).parent / "prompts"


class CriticVerdict(BaseModel):
    satisfies_goal: bool = Field(
        description="True iff the draft response answers the user's full goal, grounded in tool outputs."
    )
    reason: str = Field(description="One-sentence summary of the verdict. The user does not see this directly.")
    concrete_complaints: list[str] = Field(
        default_factory=list,
        description=(
            "Specific, actionable issues if satisfies_goal=false. Empty if satisfies_goal=true. "
            "Each item should be a specific failure (a missed sub-goal, an ungrounded claim, "
            "a skipped step) the replanner can act on."
        ),
    )


SUBMIT_VERDICT_TOOL: dict = {
    "name": "submit_verdict",
    "description": (
        "Submit your final verdict on whether the draft response satisfies the goal. "
        "This is how you respond — there is no free-text output."
    ),
    "input_schema": CriticVerdict.model_json_schema(),
}


def _load_critic_prompt() -> str:
    return (_PROMPTS_DIR / "critic.md").read_text(encoding="utf-8")


def _serialize_step(step: AgentStep) -> dict:
    d = asdict(step)
    return {k: v for k, v in d.items() if v is not None}


def _build_critic_messages(
    goal: str,
    plan: Plan | None,
    steps: list[AgentStep],
    draft_response: str,
) -> list[dict]:
    plan_block = (
        plan.model_dump_json(indent=2)
        if plan is not None
        else "(no structured plan was produced — single-step or trivial goal)"
    )
    # Critic sees only execution and outcome steps — LLM-call records are noise here.
    visible_step_types = {"tool_call", "tool_error", "refusal", "final"}
    relevant_steps = [_serialize_step(s) for s in steps if s.type in visible_step_types]

    content = (
        f"# Goal\n\n{goal}\n\n"
        f"# Plan\n\n```json\n{plan_block}\n```\n\n"
        f"# Recorded steps\n\n```json\n{relevant_steps}\n```\n\n"
        f"# Draft response\n\n{draft_response}\n\n"
        "Emit your verdict via `submit_verdict`."
    )
    return [{"role": "user", "content": content}]


class CriticResult(BaseModel):
    verdict: CriticVerdict
    usage: UsageSummary
    raw_input: dict
    model_config = {"arbitrary_types_allowed": True}


async def evaluate(
    *,
    goal: str,
    plan: Plan | None,
    steps: list[AgentStep],
    draft_response: str,
    llm: LLM,
    model: str,
) -> CriticResult:
    system = _load_critic_prompt()
    messages = _build_critic_messages(goal, plan, steps, draft_response)
    response = await llm.complete(
        model=model,
        system=system,
        messages=messages,
        tools=[SUBMIT_VERDICT_TOOL],
        tool_choice={"type": "tool", "name": "submit_verdict"},
        max_tokens=1024,
    )

    usage = summarize_usage(model, response)

    tool_use_block = next(
        (b for b in response.content if b.type == "tool_use" and b.name == "submit_verdict"),
        None,
    )
    if tool_use_block is None:
        raise ValueError(
            f"Critic did not call submit_verdict. Response content types: {[b.type for b in response.content]}"
        )
    raw = tool_use_block.input if isinstance(tool_use_block.input, dict) else {}
    try:
        verdict = CriticVerdict.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"Critic produced an invalid verdict: {e}") from e

    return CriticResult(verdict=verdict, usage=usage, raw_input=raw)
