"""Approval gate: decides whether a plan needs human confirmation before execution.

A plan requires approval if any step's expected_tool is marked `destructive=True` or
`cost_hint="expensive"` in the tool registry. Cheap, non-destructive tools run without
asking — anything else pauses the agent and waits for the user.

This module only computes the requirement. The agent loop (`agent/loop.py`) is responsible
for actually pausing, persisting the pending plan, and surfacing it to the API. The API
layer (`api/agent.py`) handles the resume after the user decides.

Approval is checked ONLY on the initial plan in this phase. A replanner's revised plan
inherits the original approval scope — the prompt directs the replanner to stay within
the approved tool set. If we hit cases where the replanner wants to introduce a new
expensive tool we will re-prompt for approval; for now this is documented and not
defended against.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from bioforge.agent.planner import Plan
from bioforge.tools.base import ToolSpec


class ApprovalRequirement(BaseModel):
    required: bool = Field(description="True if the plan needs explicit user approval.")
    reasons: list[str] = Field(
        default_factory=list,
        description=(
            "Human-readable reasons (one per offending step). Empty when "
            "`required=False`. Shown to the user in the approval card."
        ),
    )


def requires_approval(plan: Plan | None, registry: dict[str, ToolSpec]) -> ApprovalRequirement:
    if plan is None or not plan.steps:
        return ApprovalRequirement(required=False)

    reasons: list[str] = []
    for step in plan.steps:
        if not step.expected_tool:
            continue
        spec = registry.get(step.expected_tool)
        if spec is None:
            # Unknown tools are handled (and refused) by the executor itself; the
            # approval gate is silent about them so the user sees the more accurate
            # "not registered" error rather than a confusing "approve unknown tool".
            continue
        if spec.destructive:
            reasons.append(
                f"Step {step.idx} ({step.expected_tool}): destructive — may modify or "
                "delete user data."
            )
        if spec.cost_hint == "expensive":
            reasons.append(
                f"Step {step.idx} ({step.expected_tool}): expensive — long runtime "
                "and/or external API cost."
            )

    return ApprovalRequirement(required=bool(reasons), reasons=reasons)
