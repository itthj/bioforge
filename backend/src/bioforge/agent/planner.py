"""Planner: produces a structured `Plan` for a goal before the executor runs.

Uses forced tool-use (`tool_choice={"type":"tool","name":"submit_plan"}`) to coerce
Claude into emitting structured JSON that we validate against the `Plan` schema. This is
the idiomatic way to get reliable structured outputs from the Anthropic API.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from bioforge.agent.llm import LLM, UsageSummary, summarize_usage
from bioforge.tools.base import ToolSpec

_PROMPTS_DIR = Path(__file__).parent / "prompts"


class PlanStep(BaseModel):
    idx: int = Field(description="0-based step index.")
    description: str = Field(description="What this step does, in plain English.")
    expected_tool: str | None = Field(
        default=None,
        description="The tool that will run this step, or null for non-tool reasoning steps.",
    )
    rationale: str = Field(
        description="Why this step is here and how its output feeds the next. One sentence."
    )


class Plan(BaseModel):
    is_trivial: bool = Field(
        description="True if the goal is a single-tool call with no chaining."
    )
    summary: str = Field(description="One-sentence description of the approach.")
    steps: list[PlanStep] = Field(default_factory=list)


SUBMIT_PLAN_TOOL: dict = {
    "name": "submit_plan",
    "description": (
        "Submit your final plan for the user's goal. This is how you respond — there is "
        "no free-text output."
    ),
    "input_schema": Plan.model_json_schema(),
}


def _load_planner_prompt() -> str:
    return (_PROMPTS_DIR / "planner.md").read_text(encoding="utf-8")


def _format_tools_for_planner(tools: list[ToolSpec]) -> str:
    if not tools:
        return "(no tools are registered)"
    lines = []
    for t in tools:
        tags = f" [tags: {', '.join(t.tags)}]" if t.tags else ""
        lines.append(f"- **{t.name}**{tags}: {t.description}")
    return "\n".join(lines)


def _build_planner_messages(
    goal: str, tools: list[ToolSpec], memory_context: str = ""
) -> list[dict]:
    tools_block = _format_tools_for_planner(tools)
    parts = [f"# Goal\n\n{goal}"]
    if memory_context.strip():
        parts.append(memory_context.strip())
    parts.append(f"# Available tools\n\n{tools_block}")
    parts.append("Emit your plan by calling `submit_plan`.")
    return [{"role": "user", "content": "\n\n".join(parts)}]


class PlannerResult(BaseModel):
    plan: Plan
    usage: UsageSummary
    raw_input: dict
    model_config = {"arbitrary_types_allowed": True}


async def make_plan(
    goal: str,
    *,
    llm: LLM,
    model: str,
    available_tools: list[ToolSpec],
    memory_context: str = "",
) -> PlannerResult:
    """Call the planner LLM, force `submit_plan`, validate, and return the structured plan.

    `memory_context` is an optional markdown block describing project state (organism,
    reference genome, persisted memory entries). When non-empty it's appended to the
    planner's user message — the planner uses it to make better-informed plans without
    burning a tool call to look things up.

    Raises `ValueError` if the model fails to call submit_plan or returns invalid input.
    The caller (the agent loop) catches and records this as a planning error in the trace.
    """
    system = _load_planner_prompt()
    messages = _build_planner_messages(goal, available_tools, memory_context)
    response = await llm.complete(
        model=model,
        system=system,
        messages=messages,
        tools=[SUBMIT_PLAN_TOOL],
        tool_choice={"type": "tool", "name": "submit_plan"},
        max_tokens=2048,
    )

    usage = summarize_usage(model, response)

    tool_use_block = next(
        (b for b in response.content if b.type == "tool_use" and b.name == "submit_plan"),
        None,
    )
    if tool_use_block is None:
        raise ValueError(
            "Planner did not call submit_plan. "
            f"Response content types: {[b.type for b in response.content]}"
        )

    raw = tool_use_block.input if isinstance(tool_use_block.input, dict) else {}
    try:
        plan = Plan.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"Planner produced an invalid Plan: {e}") from e

    return PlannerResult(plan=plan, usage=usage, raw_input=raw)
