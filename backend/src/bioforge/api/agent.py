from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import asdict, fields
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bioforge.agent import AgentResult, AgentStep, Plan, resume_agent, run_agent
from bioforge.agent.context import AgentContextScope
from bioforge.agent.jobs import create_queued_trace
from bioforge.agent.llm import LLM
from bioforge.api.auth import get_current_user, require_project_access
from bioforge.api.sse import format_event, format_keepalive
from bioforge.api.usage import enforce_run_quota
from bioforge.config import settings
from bioforge.constants import DEFAULT_PROJECT_ID
from bioforge.db.engine import get_session
from bioforge.db.models import Trace, User
from bioforge.provenance import (
    build_run_manifest,
    render_methods_draft,
    render_methods_report,
    render_reproduce_script,
    to_ro_crate,
)

router = APIRouter()

# How long the SSE loop waits on the queue before flushing a keepalive comment. Short
# enough that intermediate proxies don't drop the connection during a slow BLAST run;
# long enough that we don't spam the wire with empty lines on a fast trivial goal.
_SSE_KEEPALIVE_SECONDS = 15.0

# How often GET /agent/{id}/stream re-reads the Trace while a job runs in the worker. ~0.5s is
# the documented latency floor of the DB-polling design (plan B1); fine for a research tool.
_STREAM_POLL_SECONDS = 0.5
# Backstop for a worker that died WITHOUT writing a terminal state: once a job has been non-terminal
# for longer than the worker's hard time limit plus this margin, the stream reports staleness
# honestly (never a fake `completed`) and stops. The worker's own soft-time-limit normally flips a
# stuck job to `error` first; this only catches a truly lost worker.
_STREAM_STALE_MARGIN_SECONDS = 60.0


class AgentRunRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=10_000)
    project_id: str = Field(default=DEFAULT_PROJECT_ID, max_length=64)
    autonomy: Literal["auto", "review"] = Field(
        default="auto",
        description=(
            "Autonomy level. 'auto' pauses only for expensive/destructive plans; "
            "'review' pauses after planning on any non-trivial plan so the user "
            "approves the plan before any tool runs."
        ),
    )


class AgentApproveRequest(BaseModel):
    approved: bool = Field(description="True to approve and execute; false to cancel the run.")
    reason: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional user note recorded in the trace.",
    )
    plan: dict | None = Field(
        default=None,
        description=(
            "Optionally-edited plan to resume with (same shape as the proposed plan). "
            "When provided on an approval, it REPLACES the originally-proposed plan as the "
            "guidance fed to the executor. Note: the executor is a free-form tool-use loop "
            "that treats the plan as guidance and may adapt it, so editing steers the run "
            "rather than hard-constraining it. Omit to approve the plan as proposed."
        ),
    )


def _resolve_resume_plan(trace: Trace, body: AgentApproveRequest) -> tuple[Plan | None, tuple[int, str] | None]:
    """Pick + validate the plan to resume an approved run with.

    Prefers the user's edited plan (`body.plan`) when supplied, else the originally-proposed
    plan persisted on the trace. Returns ``(plan, None)`` on success or ``(None, (status, detail))``
    so each caller can map the failure to its own protocol (HTTP error vs SSE error event). An
    invalid EDITED plan is a 400 (client error); a persisted plan that fails re-validation is a
    500 (we stored something bad).
    """
    raw_plan = body.plan if body.plan is not None else trace.awaiting_approval_plan
    if raw_plan is None:
        return None, (500, "Trace was pending_approval but no plan was persisted; cannot resume.")
    try:
        return Plan.model_validate(raw_plan), None
    except PydanticValidationError as e:
        if body.plan is not None:
            return None, (400, f"Edited plan is invalid: {e}")
        return None, (500, f"Persisted plan failed re-validation: {e}")


class AgentRunResponse(BaseModel):
    trace_id: str
    goal: str
    project_id: str
    status: str
    response_text: str
    model: str
    steps: list[dict]
    usage: dict | None
    pending_plan: dict | None = None
    approval_reasons: list[str] = []


def _trace_to_response(trace: Trace, result: AgentResult | None = None) -> AgentRunResponse:
    """Render a Trace row as an API response.

    `result` is passed when the trace was just produced and we have the usage object
    in memory; falls back to the persisted columns otherwise.
    """
    usage = (
        asdict(result.usage)
        if result and result.usage
        else {
            "input_tokens": trace.tokens_input,
            "output_tokens": trace.tokens_output,
            "cache_creation_tokens": trace.tokens_cache_creation,
            "cache_read_tokens": trace.tokens_cache_read,
            "cost_usd": trace.cost_usd,
            "model": trace.model,
        }
    )
    return AgentRunResponse(
        trace_id=trace.id,
        goal=trace.goal,
        project_id=trace.project_id,
        status=trace.status,
        response_text=trace.response_text,
        model=trace.model,
        steps=trace.steps,
        usage=usage,
        pending_plan=trace.awaiting_approval_plan,
        approval_reasons=trace.approval_reasons or [],
    )


async def _persist_new_trace(session: AsyncSession, result: AgentResult) -> Trace:
    trace = Trace(
        project_id=result.project_id,
        goal=result.goal,
        response_text=result.response_text,
        status=result.status,
        model=result.model,
        steps=[asdict(s) for s in result.steps],
        tokens_input=result.usage.input_tokens if result.usage else 0,
        tokens_output=result.usage.output_tokens if result.usage else 0,
        tokens_cache_creation=result.usage.cache_creation_tokens if result.usage else 0,
        tokens_cache_read=result.usage.cache_read_tokens if result.usage else 0,
        cost_usd=result.usage.cost_usd if result.usage else 0.0,
        awaiting_approval_plan=result.pending_plan,
        approval_reasons=list(result.approval_reasons or []),
        completed_at=datetime.now(UTC),
    )
    session.add(trace)
    await session.flush()
    return trace


def get_llm() -> LLM:
    """Overridable FastAPI dependency. Tests inject a mocked LLM via app.dependency_overrides."""
    return LLM()


def _celery_mode() -> bool:
    """True when runs should be dispatched to the Celery worker pool. Read at call time
    (not import time) so tests can flip BIOFORGE_TASK_QUEUE via the Settings singleton."""
    return settings.task_queue.strip().lower() == "celery"


async def _authorize_existing_trace(session: AsyncSession, trace_id: str, current_user: User) -> None:
    """Pre-flight access check for the streaming endpoints: if the trace exists, enforce that the
    current user may access its project (404 otherwise). A genuinely missing trace is left to the
    stream generator, which reports it as an SSE error event -- the established behavior."""
    trace = (await session.execute(select(Trace).where(Trace.id == trace_id))).scalar_one_or_none()
    if trace is not None:
        await require_project_access(session, trace.project_id, current_user)


async def _enqueue_agent_run(body: AgentRunRequest, session: AsyncSession) -> AgentRunResponse:
    """Celery mode: persist a ``queued`` Trace, enqueue the durable run job, and return the
    trace_id IMMEDIATELY (status ``queued``). The client then watches it via
    ``GET /agent/{trace_id}/stream``. The queued row is committed BEFORE enqueueing so the
    worker -- a separate process -- can load it; the task id is recorded for cancellation."""
    trace = await create_queued_trace(session, goal=body.goal, project_id=body.project_id, backend="celery")
    await session.commit()  # the worker reads this row from another connection -- it must be durable first.

    # apply_async (not send_task-by-name): the task lives in our own codebase, so referencing it
    # directly is cleaner AND honors task_always_eager for the hermetic tests. send_task always
    # publishes to the broker even in eager mode.
    from bioforge.tasks.celery_app import run_agent_job_task

    async_result = run_agent_job_task.apply_async(
        args=[trace.id, body.goal, body.project_id, body.autonomy],
    )
    # Only task_id changed since the last commit, so this UPDATE touches that column alone and
    # cannot clobber a status the worker may already have advanced (matters under eager mode).
    trace.task_id = async_result.id
    await session.commit()
    return _trace_to_response(trace)


async def _enqueue_and_stream(body: AgentRunRequest, session: AsyncSession) -> AsyncIterator[str]:
    """Celery mode for the SSE entrypoint: enqueue the run, announce its trace_id immediately via
    a ``queued`` event (so the client can wire Stop -> /cancel), then DELEGATE to the same Trace
    poller the standalone /stream endpoint uses. This keeps the frontend's run entrypoint identical
    between inline and celery -- only the transport changes."""
    trace = await create_queued_trace(session, goal=body.goal, project_id=body.project_id, backend="celery")
    await session.commit()  # durable before the worker (another process) loads it.

    from bioforge.tasks.celery_app import run_agent_job_task

    async_result = run_agent_job_task.apply_async(
        args=[trace.id, body.goal, body.project_id, body.autonomy],
    )
    trace.task_id = async_result.id
    await session.commit()

    # The client learns the trace_id here (the poller's step events don't carry it), which is what
    # the Stop button needs to revoke the right Celery task.
    yield format_event("queued", {"trace_id": trace.id, "status": "queued", "job_backend": "celery"})
    async for event in _stream_trace_progress(trace_id=trace.id, session=session):
        yield event


async def _enqueue_approved_resume(
    trace: Trace, plan: Plan, decision_step: dict, session: AsyncSession
) -> AgentRunResponse:
    """Celery mode for an approved resume (slice 6): append the decision step, persist the approved
    plan, flip the trace to ``running``, enqueue the durable resume job, and return immediately. The
    resumed steps are followed via GET /agent/{id}/stream (or the approve/stream variant). Mirrors
    the in-request approve handler's bookkeeping, but the executor runs in a worker."""
    trace.awaiting_approval_plan = plan.model_dump()
    trace.steps = list(trace.steps) + [decision_step]
    step_idx_start = len(trace.steps)  # resumed steps start right after the decision step
    trace.status = "running"
    await session.commit()  # durable before the worker (another process) loads it.

    from bioforge.tasks.celery_app import resume_agent_job_task

    async_result = resume_agent_job_task.apply_async(args=[trace.id, plan.model_dump(), step_idx_start])
    trace.task_id = async_result.id
    await session.commit()
    return _trace_to_response(trace)


@router.post("/agent/run", response_model=AgentRunResponse)
async def agent_run(
    body: AgentRunRequest,
    session: AsyncSession = Depends(get_session),
    llm: LLM = Depends(get_llm),
    current_user: User = Depends(get_current_user),
) -> AgentRunResponse:
    await require_project_access(session, body.project_id, current_user)
    await enforce_run_quota(session, current_user)
    if _celery_mode():
        return await _enqueue_agent_run(body, session)
    with AgentContextScope(project_id=body.project_id, session=session):
        result = await run_agent(body.goal, project_id=body.project_id, llm=llm, autonomy=body.autonomy)
    trace = await _persist_new_trace(session, result)
    return _trace_to_response(trace, result)


def _done_payload(trace: Trace, result: AgentResult) -> dict:
    """Final SSE `done` event — same shape as AgentRunResponse but as a dict so
    json.dumps can render it inline without instantiating the Pydantic model."""
    return {
        "trace_id": trace.id,
        "status": result.status,
        "response_text": result.response_text,
        "model": result.model,
        "usage": asdict(result.usage) if result.usage else None,
        "pending_plan": result.pending_plan,
        "approval_reasons": result.approval_reasons,
    }


async def _stream_agent_run(
    *,
    goal: str,
    project_id: str,
    autonomy: Literal["auto", "review"],
    session: AsyncSession,
    llm: LLM,
) -> AsyncIterator[str]:
    """Run `run_agent` in a background task, ferry each AgentStep out as an SSE `step`
    event, persist the trace once the task finishes, then emit `done`.

    Keep-alive comments flush every ~15s of idleness so proxies and clients don't drop
    a long BLAST connection.
    """
    queue: asyncio.Queue = asyncio.Queue()
    DONE = object()  # sentinel — distinct from any payload kind

    async def emit_step(step: AgentStep) -> None:
        await queue.put(("step", step))

    async def runner() -> None:
        try:
            with AgentContextScope(project_id=project_id, session=session):
                result = await run_agent(goal, project_id=project_id, llm=llm, autonomy=autonomy, on_step=emit_step)
            await queue.put(("result", result))
        except Exception as e:  # noqa: BLE001 — caught & reported, then re-emitted
            await queue.put(("error", f"{type(e).__name__}: {e}"))
        finally:
            await queue.put(DONE)

    task = asyncio.create_task(runner())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=_SSE_KEEPALIVE_SECONDS)
            except TimeoutError:
                yield format_keepalive()
                continue

            if item is DONE:
                return
            kind, payload = item  # type: ignore[misc]
            if kind == "step":
                yield format_event("step", asdict(payload))
            elif kind == "result":
                trace = await _persist_new_trace(session, payload)
                yield format_event("done", _done_payload(trace, payload))
            elif kind == "error":
                yield format_event("error", {"message": payload})
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


@router.post("/agent/run/stream")
async def agent_run_stream(
    body: AgentRunRequest,
    session: AsyncSession = Depends(get_session),
    llm: LLM = Depends(get_llm),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """SSE variant of /agent/run. Emits `step` events as they happen, ends with a
    `done` event carrying the trace_id, response_text, usage, and (if applicable)
    pending_plan + approval_reasons for the approval-gate path.

    In celery mode the run executes in a worker and we stream its persisted progress (preceded by
    a `queued` event); inline mode runs it in-process as before."""
    await require_project_access(session, body.project_id, current_user)
    await enforce_run_quota(session, current_user)
    generator = (
        _enqueue_and_stream(body, session)
        if _celery_mode()
        else _stream_agent_run(
            goal=body.goal,
            project_id=body.project_id,
            autonomy=body.autonomy,
            session=session,
            llm=llm,
        )
    )
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/agent/{trace_id}/approve", response_model=AgentRunResponse)
async def agent_approve(
    trace_id: str,
    body: AgentApproveRequest,
    session: AsyncSession = Depends(get_session),
    llm: LLM = Depends(get_llm),
    current_user: User = Depends(get_current_user),
) -> AgentRunResponse:
    """Resume a paused agent run. The trace must be in `pending_approval` state."""
    trace = (await session.execute(select(Trace).where(Trace.id == trace_id))).scalar_one_or_none()
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id!r} not found")
    await require_project_access(session, trace.project_id, current_user)
    if trace.status != "pending_approval":
        raise HTTPException(
            status_code=409,
            detail=(f"Trace {trace_id!r} is not awaiting approval (current status: {trace.status!r})"),
        )

    # Record the decision in the step trail regardless of which way the user went.
    decision_step = {
        "idx": len(trace.steps),
        "type": "approval_decision",
        "duration_ms": 0,
        "approved": body.approved,
        "error": body.reason if body.reason else None,
        "plan_edited": bool(body.approved and body.plan is not None),
    }

    if not body.approved:
        trace.status = "cancelled"
        trace.response_text = "User declined to approve the plan. No tools were run."
        trace.steps = list(trace.steps) + [decision_step]
        trace.awaiting_approval_plan = None
        trace.completed_at = datetime.now(UTC)
        await session.flush()
        return _trace_to_response(trace)

    # Approved — resume with the edited plan if supplied, else the proposed one.
    plan, error = _resolve_resume_plan(trace, body)
    if error is not None:
        raise HTTPException(status_code=error[0], detail=error[1])
    assert plan is not None  # error is None => plan is set

    if _celery_mode():
        return await _enqueue_approved_resume(trace, plan, decision_step, session)

    # Persist the approved (possibly edited) plan before resuming, so a failure mid-run leaves
    # the trace reflecting exactly what the user signed off on.
    trace.awaiting_approval_plan = plan.model_dump()

    step_idx_start = len(trace.steps) + 1  # +1 because we're about to append decision_step

    with AgentContextScope(project_id=trace.project_id, session=session):
        new_result = await resume_agent(
            goal=trace.goal,
            plan=plan,
            project_id=trace.project_id,
            step_idx_start=step_idx_start,
            llm=llm,
        )

    new_step_dicts = [asdict(s) for s in new_result.steps]
    trace.steps = list(trace.steps) + [decision_step] + new_step_dicts
    trace.status = new_result.status
    trace.response_text = new_result.response_text
    trace.awaiting_approval_plan = None
    trace.completed_at = datetime.now(UTC)
    if new_result.usage is not None:
        trace.tokens_input += new_result.usage.input_tokens
        trace.tokens_output += new_result.usage.output_tokens
        trace.tokens_cache_creation += new_result.usage.cache_creation_tokens
        trace.tokens_cache_read += new_result.usage.cache_read_tokens
        trace.cost_usd = round(trace.cost_usd + new_result.usage.cost_usd, 6)
    await session.flush()

    return _trace_to_response(trace, new_result)


def _done_payload_from_trace(trace: Trace) -> dict:
    """Build a `done` SSE payload from a persisted Trace row alone — used for paths
    (like a /approve cancel) where there's no AgentResult to read from."""
    return {
        "trace_id": trace.id,
        "status": trace.status,
        "response_text": trace.response_text,
        "model": trace.model,
        "usage": {
            "input_tokens": trace.tokens_input,
            "output_tokens": trace.tokens_output,
            "cache_creation_tokens": trace.tokens_cache_creation,
            "cache_read_tokens": trace.tokens_cache_read,
            "cost_usd": trace.cost_usd,
            "model": trace.model,
        },
        "pending_plan": trace.awaiting_approval_plan,
        "approval_reasons": trace.approval_reasons or [],
    }


async def _stream_agent_approve(
    *,
    trace_id: str,
    approved: bool,
    reason: str | None,
    edited_plan: dict | None,
    session: AsyncSession,
    llm: LLM,
) -> AsyncIterator[str]:
    """SSE variant of /agent/{trace_id}/approve. Emits a `step` for the approval
    decision, then (if approved) streams each step of the resumed execution, then
    `done`. Errors land as `error` events and are also recorded on the trace."""
    trace = (await session.execute(select(Trace).where(Trace.id == trace_id))).scalar_one_or_none()
    if trace is None:
        yield format_event("error", {"message": f"Trace {trace_id!r} not found"})
        return
    if trace.status != "pending_approval":
        yield format_event(
            "error",
            {"message": (f"Trace {trace_id!r} is not awaiting approval (status={trace.status!r})")},
        )
        return

    decision_step_dict = {
        "idx": len(trace.steps),
        "type": "approval_decision",
        "duration_ms": 0,
        "approved": approved,
        "error": reason if reason else None,
        "plan_edited": bool(approved and edited_plan is not None),
    }
    yield format_event("step", decision_step_dict)

    if not approved:
        trace.status = "cancelled"
        trace.response_text = "User declined to approve the plan. No tools were run."
        trace.steps = list(trace.steps) + [decision_step_dict]
        trace.awaiting_approval_plan = None
        trace.completed_at = datetime.now(UTC)
        await session.flush()
        yield format_event("done", _done_payload_from_trace(trace))
        return

    # Approved — resume with the edited plan if supplied, else the proposed one.
    plan, error = _resolve_resume_plan(trace, AgentApproveRequest(approved=True, reason=reason, plan=edited_plan))
    if error is not None:
        yield format_event("error", {"message": error[1]})
        return
    assert plan is not None  # error is None => plan is set

    if _celery_mode():
        # Persist the decision + approved plan, enqueue the durable resume job, then poll the
        # resumed steps. The decision step was already emitted above, so stream from AFTER it
        # (start_index) to avoid re-sending the plan/approval/decision the client already has.
        trace.awaiting_approval_plan = plan.model_dump()
        trace.steps = list(trace.steps) + [decision_step_dict]
        start_index = len(trace.steps)
        trace.status = "running"
        await session.commit()

        from bioforge.tasks.celery_app import resume_agent_job_task

        async_result = resume_agent_job_task.apply_async(args=[trace.id, plan.model_dump(), start_index])
        trace.task_id = async_result.id
        await session.commit()

        # Let the client wire Stop -> /cancel for the resumed run.
        yield format_event("queued", {"trace_id": trace.id, "status": "running", "job_backend": "celery"})
        async for event in _stream_trace_progress(trace_id=trace.id, session=session, start_index=start_index):
            yield event
        return

    # Persist the approved (possibly edited) plan before resuming (see the sync handler).
    trace.awaiting_approval_plan = plan.model_dump()

    step_idx_start = len(trace.steps) + 1  # +1 for decision_step we just yielded

    queue: asyncio.Queue = asyncio.Queue()
    DONE = object()

    async def emit_step(step: AgentStep) -> None:
        await queue.put(("step", step))

    async def runner() -> None:
        try:
            with AgentContextScope(project_id=trace.project_id, session=session):
                result = await resume_agent(
                    goal=trace.goal,
                    plan=plan,
                    project_id=trace.project_id,
                    step_idx_start=step_idx_start,
                    llm=llm,
                    on_step=emit_step,
                )
            await queue.put(("result", result))
        except Exception as e:  # noqa: BLE001
            await queue.put(("error", f"{type(e).__name__}: {e}"))
        finally:
            await queue.put(DONE)

    task = asyncio.create_task(runner())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=_SSE_KEEPALIVE_SECONDS)
            except TimeoutError:
                yield format_keepalive()
                continue
            if item is DONE:
                return
            kind, payload = item  # type: ignore[misc]
            if kind == "step":
                yield format_event("step", asdict(payload))
            elif kind == "result":
                new_step_dicts = [asdict(s) for s in payload.steps]
                trace.steps = list(trace.steps) + [decision_step_dict] + new_step_dicts
                trace.status = payload.status
                trace.response_text = payload.response_text
                trace.awaiting_approval_plan = None
                trace.completed_at = datetime.now(UTC)
                if payload.usage is not None:
                    trace.tokens_input += payload.usage.input_tokens
                    trace.tokens_output += payload.usage.output_tokens
                    trace.tokens_cache_creation += payload.usage.cache_creation_tokens
                    trace.tokens_cache_read += payload.usage.cache_read_tokens
                    trace.cost_usd = round(trace.cost_usd + payload.usage.cost_usd, 6)
                await session.flush()
                yield format_event("done", _done_payload(trace, payload))
            elif kind == "error":
                yield format_event("error", {"message": payload})
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


@router.post("/agent/{trace_id}/approve/stream")
async def agent_approve_stream(
    trace_id: str,
    body: AgentApproveRequest,
    session: AsyncSession = Depends(get_session),
    llm: LLM = Depends(get_llm),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """SSE variant of the approve endpoint. Streams the resumed execution after
    approval; emits a single `step`+`done` pair on cancel."""
    await _authorize_existing_trace(session, trace_id, current_user)
    return StreamingResponse(
        _stream_agent_approve(
            trace_id=trace_id,
            approved=body.approved,
            reason=body.reason,
            edited_plan=body.plan,
            session=session,
            llm=llm,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- Durable-job progress stream (Celery phase, slice 4) -----------------------------
#
# When a run executes in a Celery worker (another process), the API can't read its `on_step`
# callbacks from memory. Instead the worker persists each step to the Trace (committing as it
# goes, see agent/jobs.py), and this endpoint POLLS that row, relaying new steps as SSE until
# the job is terminal. The same endpoint catches up a finished run (reconnect / history replay):
# it emits everything already persisted, sees a terminal status, and ends.


def _is_job_terminal(status: str) -> bool:
    """A run job is terminal once it leaves the queued/running lifecycle. `pending_approval` is
    terminal for streaming -- the run paused for the user and resumes later via /approve."""
    return status not in ("queued", "running")


async def _refetch_trace(session: AsyncSession, trace_id: str) -> Trace | None:
    """Read the Trace fresh so commits from the worker's OWN connection are visible: end our
    transaction (rollback) and force the row to repopulate from the DB rather than the identity
    map. The stream never writes, so the rollback is a cheap snapshot reset, not data loss."""
    await session.rollback()
    result = await session.execute(select(Trace).where(Trace.id == trace_id).execution_options(populate_existing=True))
    return result.scalar_one_or_none()


async def _stream_trace_progress(*, trace_id: str, session: AsyncSession, start_index: int = 0) -> AsyncIterator[str]:
    """Poll a Trace and emit its steps from ``start_index`` onward as SSE until terminal.

    ``start_index`` lets the resume path skip the steps the client already has (plan + approval +
    the decision step it was just sent), emitting only the newly-resumed steps; a fresh run leaves
    it 0 to catch up everything."""
    trace = await _refetch_trace(session, trace_id)
    if trace is None:
        yield format_event("error", {"message": f"Trace {trace_id!r} not found"})
        return

    # Catch-up: emit everything already persisted from start_index, in order.
    emitted = start_index
    for step in trace.steps[start_index:]:
        yield format_event("step", step)
        emitted += 1
    if _is_job_terminal(trace.status):
        yield format_event("done", _done_payload_from_trace(trace))
        return

    # Live: poll until terminal, emitting each newly-persisted step.
    start = time.monotonic()
    last_emit = start
    max_wall = settings.celery_task_time_limit + _STREAM_STALE_MARGIN_SECONDS
    while True:
        await asyncio.sleep(_STREAM_POLL_SECONDS)
        trace = await _refetch_trace(session, trace_id)
        if trace is None:  # deleted out from under us -- vanishingly unlikely, but be honest.
            yield format_event("error", {"message": f"Trace {trace_id!r} disappeared mid-stream"})
            return

        new_steps = trace.steps[emitted:]
        if new_steps:
            for step in new_steps:
                yield format_event("step", step)
            emitted += len(new_steps)
            last_emit = time.monotonic()

        if _is_job_terminal(trace.status):
            yield format_event("done", _done_payload_from_trace(trace))
            return

        now = time.monotonic()
        if now - start > max_wall:
            yield format_event(
                "error",
                {
                    "message": (
                        "Job is still not terminal past the worker time limit; the worker may "
                        f"have died. Last known status: {trace.status!r}."
                    )
                },
            )
            # Emit the current (non-terminal) state honestly -- never a fabricated `completed`.
            yield format_event("done", _done_payload_from_trace(trace))
            return

        if now - last_emit > _SSE_KEEPALIVE_SECONDS:
            yield format_keepalive()
            last_emit = now


@router.get("/agent/{trace_id}/stream")
async def agent_stream(
    trace_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """Stream a durable run's progress by polling its Trace. Emits each persisted step as an SSE
    `step` event until the job reaches a terminal state, then a `done` event. Works for a LIVE job
    (a Celery worker writing concurrently) and to CATCH UP a finished one (reconnect / replay)."""
    await _authorize_existing_trace(session, trace_id, current_user)
    return StreamingResponse(
        _stream_trace_progress(trace_id=trace_id, session=session),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- Cancel a durable job (Celery phase, slice 5) ------------------------------------


@router.post("/agent/{trace_id}/cancel", response_model=AgentRunResponse)
async def agent_cancel(
    trace_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> AgentRunResponse:
    """Cancel a running/queued Celery-backed run: revoke the worker task (SIGTERM) and mark the
    Trace `cancelled`. The status write is authoritative -- it owns the transition regardless of
    whether the SIGTERM lands cleanly; whatever steps the worker already committed remain as an
    honest partial trace. This is what the P1b Stop button calls in celery mode.

    Inline runs are cancelled by disconnecting the SSE stream (the backend cancels the in-process
    task on disconnect), so /cancel rejects them (409). A run that is already terminal -- or paused
    at `pending_approval`, which is declined via /approve -- is not cancellable here (409)."""
    trace = (await session.execute(select(Trace).where(Trace.id == trace_id))).scalar_one_or_none()
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id!r} not found")
    await require_project_access(session, trace.project_id, current_user)
    if trace.job_backend != "celery":
        raise HTTPException(
            status_code=409,
            detail="Inline runs are cancelled by disconnecting the stream, not via /cancel.",
        )
    if _is_job_terminal(trace.status):
        raise HTTPException(
            status_code=409,
            detail=f"Trace {trace_id!r} is not cancellable (current status: {trace.status!r}).",
        )

    # Revoke the worker task. Best-effort: the authoritative state is the status write below, so a
    # missed revoke (worker already finishing) still leaves an honest `cancelled` record.
    if trace.task_id:
        from bioforge.tasks.celery_app import celery_app

        celery_app.control.revoke(trace.task_id, terminate=True, signal="SIGTERM")

    trace.status = "cancelled"
    if not trace.response_text:
        trace.response_text = "Run cancelled by user."
    trace.awaiting_approval_plan = None
    trace.completed_at = datetime.now(UTC)
    await session.flush()
    return _trace_to_response(trace)


@router.get("/traces/{trace_id}")
async def get_trace(
    trace_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict:
    trace = await _load_trace_or_404(trace_id, session, current_user)
    return {
        "id": trace.id,
        "project_id": trace.project_id,
        "goal": trace.goal,
        "response_text": trace.response_text,
        "status": trace.status,
        "model": trace.model,
        "steps": trace.steps,
        "tokens_input": trace.tokens_input,
        "tokens_output": trace.tokens_output,
        "tokens_cache_creation": trace.tokens_cache_creation,
        "tokens_cache_read": trace.tokens_cache_read,
        "cost_usd": trace.cost_usd,
        "awaiting_approval_plan": trace.awaiting_approval_plan,
        "approval_reasons": trace.approval_reasons,
        "created_at": trace.created_at.isoformat(),
        "completed_at": trace.completed_at.isoformat(),
    }


# --- Provenance / research-object export (§10) ---------------------------------------
#
# build_run_manifest / to_ro_crate / render_methods_report all consume an AgentResult, but
# what we persist is a Trace row whose `steps` is a JSON list of dicts. _result_from_trace
# rehydrates the dataclass. Trace.steps was produced by `asdict(AgentStep)` so the keys
# line up; we filter to the dataclass's known fields so an older stored trace that predates
# a newly-added optional field still rehydrates cleanly instead of raising.

_AGENT_STEP_FIELDS = {f.name for f in fields(AgentStep)}


def _result_from_trace(trace: Trace) -> AgentResult:
    steps = [AgentStep(**{k: v for k, v in d.items() if k in _AGENT_STEP_FIELDS}) for d in (trace.steps or [])]
    return AgentResult(
        goal=trace.goal,
        project_id=trace.project_id,
        response_text=trace.response_text,
        steps=steps,
        status=trace.status,
        model=trace.model,
    )


async def _load_trace_or_404(trace_id: str, session: AsyncSession, current_user: User) -> Trace:
    result = await session.execute(select(Trace).where(Trace.id == trace_id))
    trace = result.scalar_one_or_none()
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id!r} not found")
    await require_project_access(session, trace.project_id, current_user)
    return trace


@router.get("/traces/{trace_id}/manifest")
async def get_trace_manifest(
    trace_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Content-addressed run manifest (machine-readable JSON)."""
    trace = await _load_trace_or_404(trace_id, session, current_user)
    manifest = build_run_manifest(_result_from_trace(trace))
    return manifest.model_dump()


@router.get("/traces/{trace_id}/ro-crate")
async def get_trace_ro_crate(
    trace_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    """RO-Crate 1.1 metadata document (JSON-LD) for the run."""
    trace = await _load_trace_or_404(trace_id, session, current_user)
    crate = to_ro_crate(build_run_manifest(_result_from_trace(trace)))
    return JSONResponse(
        content=crate,
        media_type="application/ld+json",
        headers={"Content-Disposition": f'attachment; filename="ro-crate-metadata-{trace_id}.json"'},
    )


@router.get("/traces/{trace_id}/report", response_class=PlainTextResponse)
async def get_trace_report(
    trace_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> PlainTextResponse:
    """Publication-grade Markdown methods/reproducibility record for the run."""
    trace = await _load_trace_or_404(trace_id, session, current_user)
    result = _result_from_trace(trace)
    report = render_methods_report(build_run_manifest(result), result)
    return PlainTextResponse(
        content=report,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="bioforge-methods-{trace_id}.md"'},
    )


@router.get("/traces/{trace_id}/script", response_class=PlainTextResponse)
async def get_trace_script(
    trace_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> PlainTextResponse:
    """Runnable Python script that re-executes the run's deterministic tool pipeline."""
    trace = await _load_trace_or_404(trace_id, session, current_user)
    script = render_reproduce_script(_result_from_trace(trace))
    return PlainTextResponse(
        content=script,
        media_type="text/x-python; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="bioforge-reproduce-{trace_id}.py"'},
    )


class MethodsDraftResponse(BaseModel):
    """JSON payload for the manuscript-ready methods draft endpoint."""

    paragraph: str = Field(description="Validated methods paragraph — past tense, inline citations [N].")
    bibtex_block: str = Field(description="All cited BibTeX entries, concatenated.")
    param_table_md: str = Field(description="Supplementary parameter table in Markdown format.")
    warnings: list[str] = Field(default_factory=list, description="Non-fatal issues from the grounding guard.")
    trace_id: str


@router.get("/traces/{trace_id}/methods-draft", response_model=MethodsDraftResponse)
async def get_trace_methods_draft(
    trace_id: str,
    polish: bool = True,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> MethodsDraftResponse:
    """Manuscript-ready methods draft: paragraph + BibTeX + parameter table.

    Returns JSON with three copy-paste-ready components:
      paragraph      — past-tense prose, inline citations, hardcoded benchmark accuracy numbers
      bibtex_block   — BibTeX for every cited tool
      param_table_md — Supplementary Methods parameter table (Markdown)

    Set ?polish=false to skip the LLM fluency pass (faster; useful in tests).
    """
    trace = await _load_trace_or_404(trace_id, session, current_user)
    result = _result_from_trace(trace)
    manifest = build_run_manifest(result)
    draft = await render_methods_draft(manifest, result, polish=polish, trace_id=trace_id)
    return MethodsDraftResponse(
        paragraph=draft.paragraph,
        bibtex_block=draft.bibtex_block,
        param_table_md=draft.param_table_md,
        warnings=draft.warnings,
        trace_id=trace_id,
    )



# --- Run history (P0) ----------------------------------------------------------------


class TraceSummary(BaseModel):
    trace_id: str
    project_id: str
    goal: str
    status: str
    model: str
    cost_usd: float
    response_preview: str = Field(description="First ~140 chars of the answer, for the history list.")
    created_at: str
    completed_at: str


def _to_trace_summary(t: Trace) -> TraceSummary:
    preview = (t.response_text or "").strip().replace("\n", " ")
    if len(preview) > 140:
        preview = preview[:140].rstrip() + "…"
    return TraceSummary(
        trace_id=t.id,
        project_id=t.project_id,
        goal=t.goal,
        status=t.status,
        model=t.model,
        cost_usd=t.cost_usd,
        response_preview=preview,
        created_at=t.created_at.isoformat(),
        completed_at=t.completed_at.isoformat(),
    )


@router.get("/projects/{project_id}/traces", response_model=list[TraceSummary])
async def list_project_traces(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None, max_length=200, description="Case-insensitive substring match on the goal."),
) -> list[TraceSummary]:
    """List a project's runs, newest first — the run-history feed. Read-only; paginated."""
    await require_project_access(session, project_id, current_user)
    stmt = select(Trace).where(Trace.project_id == project_id)
    if q:
        stmt = stmt.where(Trace.goal.ilike(f"%{q}%"))
    stmt = stmt.order_by(Trace.created_at.desc()).limit(limit).offset(offset)
    rows = (await session.execute(stmt)).scalars().all()
    return [_to_trace_summary(t) for t in rows]
