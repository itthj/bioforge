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


def make_step_persister(session: AsyncSession, trace: Trace, *, commit: bool = False):
    """Build an ``on_step`` sink that appends each :class:`AgentStep` to ``trace.steps`` and
    flips the job ``queued -> running`` on the first step (stamping ``started_at``). Persisting
    per step is what lets a polling reader watch a live job; the same callback is what a Celery
    worker passes to ``run_agent`` to stream progress into the DB.

    ``commit`` controls visibility. The default (``False``, a ``flush``) is enough when the
    READER shares this session -- the in-process proof path. A Celery worker, whose progress is
    read by the API in ANOTHER process/connection, must pass ``commit=True``: a flush alone keeps
    the rows inside an uncommitted transaction that a separate connection cannot see, so the
    polling stream would observe nothing until the run ended. Per-step commits are best-effort
    progress (``run_agent`` swallows ``on_step`` errors); :func:`finalize_trace` is the
    authoritative terminal write either way.
    """

    async def _persist(step: AgentStep) -> None:
        if trace.status == QUEUED:
            trace.status = RUNNING
            trace.started_at = datetime.now(UTC)
        # Reassign (not in-place append): SQLAlchemy only tracks JSON column mutations on
        # attribute set, matching how the rest of the codebase mutates trace.steps.
        trace.steps = list(trace.steps) + [asdict(step)]
        # expire_on_commit=False (set on every sessionmaker in the codebase) keeps `trace`
        # usable after a commit, so the next step can keep appending.
        if commit:
            await session.commit()
        else:
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


async def run_agent_job_async(
    *,
    trace_id: str,
    goal: str,
    project_id: str,
    autonomy: str = "auto",
    llm: object | None = None,
) -> dict[str, str]:
    """Execute a queued run to completion inside a Celery worker, persisting progress as it goes.

    This is the worker-side core that the ``bioforge.tasks.run_agent_job`` Celery task bridges to.
    The trace row was already created (``queued``) and committed by the enqueueing request, so we
    LOAD it by id rather than create it.

    The worker is a separate process from the API, so it owns its OWN engine + sessionmaker built
    from ``settings.db_url`` (never the request-scoped session), and disposes the engine when done
    -- which also keeps it correct under the one-event-loop-per-task model the sync bridge uses.

    Progress streams to the DB via a COMMITTING step sink so the API's polling stream (another
    connection) can see each step; :func:`finalize_trace` writes the authoritative terminal state.
    A failure flips the job to ``error`` (committed) so it never hangs in ``running``; whatever
    steps were committed before the failure remain as an honest partial trace.
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from bioforge.agent import run_agent
    from bioforge.agent.context import AgentContextScope

    engine = create_async_engine(settings.db_url, future=True)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            trace = (await session.execute(select(Trace).where(Trace.id == trace_id))).scalar_one_or_none()
            if trace is None:
                return {"trace_id": trace_id, "status": "error", "error": "trace not found"}
            try:
                with AgentContextScope(project_id=project_id, session=session):
                    result = await run_agent(
                        goal,
                        project_id=project_id,
                        llm=llm,  # type: ignore[arg-type]  # None -> run_agent builds a real LLM
                        autonomy=autonomy,  # type: ignore[arg-type]  # "auto" | "review"
                        on_step=make_step_persister(session, trace, commit=True),
                    )
                await finalize_trace(session, trace, result)
                await session.commit()
                return {"trace_id": trace_id, "status": result.status}
            except Exception as e:  # noqa: BLE001 -- recorded on the trace, never silently dropped
                await session.rollback()
                # Re-load after rollback (the object is expired) and mark the job errored so a
                # dead/throwing run does not stay 'running' forever. Committed partial steps stay.
                trace = (await session.execute(select(Trace).where(Trace.id == trace_id))).scalar_one_or_none()
                if trace is not None:
                    trace.status = "error"
                    trace.response_text = f"Job failed: {type(e).__name__}: {e}"
                    trace.completed_at = datetime.now(UTC)
                    await session.commit()
                return {"trace_id": trace_id, "status": "error"}
    finally:
        await engine.dispose()


async def finalize_resumed_trace(session: AsyncSession, trace: Trace, result: AgentResult) -> Trace:
    """Terminal write for a RESUMED run (post-approval). Unlike :func:`finalize_trace` it does NOT
    rewrite ``trace.steps`` -- the committing sink already appended the decision + resumed steps
    onto the existing plan/approval prefix -- and it ADDS the resume's usage to the totals already
    on the row (the paused run's planning tokens are already counted), mirroring the in-request
    approve handler's additive accounting."""
    trace.status = result.status
    trace.response_text = result.response_text
    if result.model:
        trace.model = result.model
    trace.awaiting_approval_plan = None
    if result.usage is not None:
        trace.tokens_input += result.usage.input_tokens
        trace.tokens_output += result.usage.output_tokens
        trace.tokens_cache_creation += result.usage.cache_creation_tokens
        trace.tokens_cache_read += result.usage.cache_read_tokens
        trace.cost_usd = round(trace.cost_usd + result.usage.cost_usd, 6)
    trace.completed_at = datetime.now(UTC)
    await session.flush()
    return trace


async def resume_agent_job_async(
    *,
    trace_id: str,
    plan: dict,
    step_idx_start: int,
    llm: object | None = None,
) -> dict[str, str]:
    """Worker-side core for resuming an APPROVED run as a durable job (the celery_app
    ``bioforge.tasks.resume_agent_job`` task bridges to it).

    The enqueueing request has already appended the approval-decision step, persisted the approved
    plan, and flipped the trace to ``running`` + committed. Here we load that trace, run
    :func:`resume_agent` from ``step_idx_start`` with a committing sink (so the polling stream sees
    the resumed steps land), and write the merged terminal state via :func:`finalize_resumed_trace`.
    Own engine/sessionmaker + dispose, same as :func:`run_agent_job_async`."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from bioforge.agent import Plan, resume_agent
    from bioforge.agent.context import AgentContextScope

    engine = create_async_engine(settings.db_url, future=True)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as session:
            trace = (await session.execute(select(Trace).where(Trace.id == trace_id))).scalar_one_or_none()
            if trace is None:
                return {"trace_id": trace_id, "status": "error", "error": "trace not found"}
            try:
                plan_obj = Plan.model_validate(plan)
                with AgentContextScope(project_id=trace.project_id, session=session):
                    result = await resume_agent(
                        goal=trace.goal,
                        plan=plan_obj,
                        project_id=trace.project_id,
                        step_idx_start=step_idx_start,
                        llm=llm,  # type: ignore[arg-type]  # None -> resume_agent builds a real LLM
                        on_step=make_step_persister(session, trace, commit=True),
                    )
                await finalize_resumed_trace(session, trace, result)
                await session.commit()
                return {"trace_id": trace_id, "status": result.status}
            except Exception as e:  # noqa: BLE001 -- recorded on the trace, never silently dropped
                await session.rollback()
                trace = (await session.execute(select(Trace).where(Trace.id == trace_id))).scalar_one_or_none()
                if trace is not None:
                    trace.status = "error"
                    trace.response_text = f"Resume failed: {type(e).__name__}: {e}"
                    trace.completed_at = datetime.now(UTC)
                    await session.commit()
                return {"trace_id": trace_id, "status": "error"}
    finally:
        await engine.dispose()
