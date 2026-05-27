"""Agent role contracts — Phase 5 foundations.

The current Phase 0-4 agent runs as a single monolithic loop in `agent/loop.py`.
Phase 5 splits the three core responsibilities (plan, execute, critique) into
separately-managed agents, each with its own Anthropic context, system prompt,
and budget. This module defines the Protocol contracts those split agents
will implement.

We codify the contracts here BEFORE making the split so:
  1. The existing in-loop functions (make_plan, _execute, _try_critique) can
     be wrapped in role objects without changing their signatures.
  2. The Phase 5 multi-agent runner can switch implementations transparently
     (in-process role object → remote sub-agent worker).
  3. Tests can assert that any future implementation honors the same shape.

Implementations are NOT provided here. The actual Planner, Executor, and
Critic classes will land in their own modules during the Phase 5 split.
This file is contracts-only on purpose — it should never grow business logic.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from bioforge.agent.critic import CriticVerdict
from bioforge.agent.planner import Plan


@dataclass
class PlanContext:
    """Everything the planner needs to make a Plan from a goal.

    Kept as an explicit dataclass — not **kwargs — so adding a new field
    (e.g. tool budget, prior-run memory) is a type-checked breaking change
    and every implementation is forced to acknowledge it.
    """

    goal: str
    model: str
    available_tools: list[dict]
    memory_context: str = ""
    project_id: str | None = None
    # Optional callback for streaming intermediate planner thoughts. Phase 5
    # may run the planner remotely, so progress goes through this hook
    # rather than direct stdout / SSE writes.
    on_progress: Callable[[str], Awaitable[None]] | None = None


@dataclass
class ExecutorContext:
    """Everything the executor needs to run a Plan and produce a result.

    The plan + the memory of previous tool results form the conversation
    history the executor LLM gets — keeping these explicit avoids accidental
    sharing of state between sub-agents.
    """

    plan: Plan
    goal: str
    model: str
    available_tools: list[dict]
    memory_context: str = ""
    project_id: str | None = None
    max_iterations: int = 10
    on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None


@dataclass
class CriticContext:
    """Everything the critic needs to judge whether a result satisfies the goal."""

    goal: str
    plan: Plan
    response_text: str
    tool_calls_made: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""
    on_progress: Callable[[str], Awaitable[None]] | None = None


@dataclass
class ExecutionResult:
    """Output of an executor run.

    `tool_calls` retains the full chronological list of (tool_name, input,
    output_or_error) tuples so the critic can reason over the trail and the
    UI can render every step.
    """

    response_text: str
    tool_calls: list[dict[str, Any]]
    iterations_used: int
    finished_with_tool_use: bool = False
    refused: bool = False
    refusal_reason: str = ""


@runtime_checkable
class Planner(Protocol):
    """Produces a Plan for a given goal. The implementation decides whether
    to plan with an LLM, a heuristic, or by replaying a previous trace."""

    async def make_plan(self, ctx: PlanContext) -> Plan: ...


@runtime_checkable
class Executor(Protocol):
    """Executes a Plan by running tool calls in a manual tool-use loop. Returns
    the user-facing response text + the trail of tool calls."""

    async def execute(self, ctx: ExecutorContext) -> ExecutionResult: ...


@runtime_checkable
class Critic(Protocol):
    """Critiques an ExecutionResult against the goal. Returns satisfies/no
    + a reason + concrete complaints the planner can use for replanning."""

    async def critique(self, ctx: CriticContext) -> CriticVerdict: ...
