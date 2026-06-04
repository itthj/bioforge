"""Durable job persistence for agent runs (Celery phase).

A run is persisted as a `Trace` row and updated INCREMENTALLY as it executes, so a separate
reader (the polling SSE endpoint, or History) sees progress on a still-running job. These
helpers are transport-agnostic: the same step sink works whether the run executes inline or in
a Celery worker. Wiring into the API + worker is a later slice; here the persistence machinery
is proven against the in-process path.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from bioforge.agent import AgentResult, AgentStep
from bioforge.config import settings
from bioforge.db.models import Trace

# Non-terminal job statuses (terminal ones are the existing AgentStatus values).
QUEUED = "queued"
RUNNING = "running"


async def create_queued_trace(
    session: AsyncSession,
    *,
    goal: str,
    project_id: str,
    backend: str = "inline",
    task_id: str | None = None,
    model: str | None = None,
) -> Trace:
    """Insert a Trace in the ``queued`` state -- the durable job row, created at submit time
    BEFORE execution begins. ``model`` defaults to the configured default model and is
    re-stamped with the run's actual model on :func:`finalize_trace`.
    """
    trace = Trace(
        project_id=project_id,
        goal=goal,
        status=QUEUED,
        model=model or settings.default_model,
        steps=[],
        job_backend=backend,
        task_id=task_id,
    )
    session.add(trace)
    await session.flush()
    return trace


def make_step_persister(session: AsyncSession, trace: Trace):
    """Build an ``on_step`` sink that appends each :class:`AgentStep` to ``trace.steps`` and
    flips the job ``queued -> running`` on the first step (stamping ``started_at``). Flushing
    per step is what lets a polling reader watch a live job; the same callback is what a Celery
    worker will pass to ``run_agent`` to stream progress into the DB.
    """

    async def _persist(step: AgentStep) -> None:
        if trace.status == QUEUED:
            trace.status = RUNNING
            trace.started_at = datetime.now(UTC)
        # Reassign (not in-place append): SQLAlchemy only tracks JSON column mutations on
        # attribute set, matching how the rest of the codebase mutates trace.steps.
        trace.steps = list(trace.steps) + [asdict(step)]
        await session.flush()

    return _persist


async def finalize_trace(session: AsyncSession, trace: Trace, result: AgentResult) -> Trace:
    """Write the terminal state of a finished run onto its Trace row: status, response text,
    the authoritative step list, usage/cost, and any pending-approval plan. Sets
    ``completed_at`` to now. Safe to call whether or not steps were streamed via the sink --
    ``result.steps`` is the canonical final list.
    """
    trace.status = result.status
    trace.response_text = result.response_text
    if result.model:
        trace.model = result.model
    trace.steps = [asdict(s) for s in result.steps]
    trace.awaiting_approval_plan = result.pending_plan
    trace.approval_reasons = list(result.approval_reasons or [])
    if result.usage is not None:
        trace.tokens_input = result.usage.input_tokens
        trace.tokens_output = result.usage.output_tokens
        trace.tokens_cache_creation = result.usage.cache_creation_tokens
        trace.tokens_cache_read = result.usage.cache_read_tokens
        trace.cost_usd = result.usage.cost_usd
    trace.completed_at = datetime.now(UTC)
    await session.flush()
    return trace
