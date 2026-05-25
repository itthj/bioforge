from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import asdict
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bioforge.agent import AgentResult, AgentStep, Plan, resume_agent, run_agent
from bioforge.agent.context import AgentContextScope
from bioforge.agent.llm import LLM
from bioforge.api.sse import format_event, format_keepalive
from bioforge.constants import DEFAULT_PROJECT_ID
from bioforge.db.engine import get_session
from bioforge.db.models import Trace

router = APIRouter()

# How long the SSE loop waits on the queue before flushing a keepalive comment. Short
# enough that intermediate proxies don't drop the connection during a slow BLAST run;
# long enough that we don't spam the wire with empty lines on a fast trivial goal.
_SSE_KEEPALIVE_SECONDS = 15.0


class AgentRunRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=10_000)
    project_id: str = Field(default=DEFAULT_PROJECT_ID, max_length=64)


class AgentApproveRequest(BaseModel):
    approved: bool = Field(
        description="True to approve and execute; false to cancel the run."
    )
    reason: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional user note recorded in the trace.",
    )


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


@router.post("/agent/run", response_model=AgentRunResponse)
async def agent_run(
    body: AgentRunRequest,
    session: AsyncSession = Depends(get_session),
    llm: LLM = Depends(get_llm),
) -> AgentRunResponse:
    with AgentContextScope(project_id=body.project_id, session=session):
        result = await run_agent(body.goal, project_id=body.project_id, llm=llm)
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
                result = await run_agent(
                    goal, project_id=project_id, llm=llm, on_step=emit_step
                )
            await queue.put(("result", result))
        except Exception as e:  # noqa: BLE001 — caught & reported, then re-emitted
            await queue.put(("error", f"{type(e).__name__}: {e}"))
        finally:
            await queue.put(DONE)

    task = asyncio.create_task(runner())
    try:
        while True:
            try:
                item = await asyncio.wait_for(
                    queue.get(), timeout=_SSE_KEEPALIVE_SECONDS
                )
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
) -> StreamingResponse:
    """SSE variant of /agent/run. Emits `step` events as they happen, ends with a
    `done` event carrying the trace_id, response_text, usage, and (if applicable)
    pending_plan + approval_reasons for the approval-gate path."""
    return StreamingResponse(
        _stream_agent_run(
            goal=body.goal, project_id=body.project_id, session=session, llm=llm
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/agent/{trace_id}/approve", response_model=AgentRunResponse)
async def agent_approve(
    trace_id: str,
    body: AgentApproveRequest,
    session: AsyncSession = Depends(get_session),
    llm: LLM = Depends(get_llm),
) -> AgentRunResponse:
    """Resume a paused agent run. The trace must be in `pending_approval` state."""
    trace = (
        await session.execute(select(Trace).where(Trace.id == trace_id))
    ).scalar_one_or_none()
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id!r} not found")
    if trace.status != "pending_approval":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Trace {trace_id!r} is not awaiting approval (current status: "
                f"{trace.status!r})"
            ),
        )

    # Record the decision in the step trail regardless of which way the user went.
    decision_step = {
        "idx": len(trace.steps),
        "type": "approval_decision",
        "duration_ms": 0,
        "approved": body.approved,
        "error": body.reason if body.reason else None,
    }

    if not body.approved:
        trace.status = "cancelled"
        trace.response_text = "User declined to approve the plan. No tools were run."
        trace.steps = list(trace.steps) + [decision_step]
        trace.awaiting_approval_plan = None
        trace.completed_at = datetime.now(UTC)
        await session.flush()
        return _trace_to_response(trace)

    # Approved — resume execution. Validate the persisted plan back into a typed Plan.
    raw_plan = trace.awaiting_approval_plan
    if raw_plan is None:
        raise HTTPException(
            status_code=500,
            detail="Trace was pending_approval but no plan was persisted; cannot resume.",
        )
    try:
        plan = Plan.model_validate(raw_plan)
    except PydanticValidationError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Persisted plan failed re-validation: {e}",
        ) from e

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
    session: AsyncSession,
    llm: LLM,
) -> AsyncIterator[str]:
    """SSE variant of /agent/{trace_id}/approve. Emits a `step` for the approval
    decision, then (if approved) streams each step of the resumed execution, then
    `done`. Errors land as `error` events and are also recorded on the trace."""
    trace = (
        await session.execute(select(Trace).where(Trace.id == trace_id))
    ).scalar_one_or_none()
    if trace is None:
        yield format_event("error", {"message": f"Trace {trace_id!r} not found"})
        return
    if trace.status != "pending_approval":
        yield format_event(
            "error",
            {
                "message": (
                    f"Trace {trace_id!r} is not awaiting approval "
                    f"(status={trace.status!r})"
                )
            },
        )
        return

    decision_step_dict = {
        "idx": len(trace.steps),
        "type": "approval_decision",
        "duration_ms": 0,
        "approved": approved,
        "error": reason if reason else None,
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

    # Approved — resume execution streamed.
    raw_plan = trace.awaiting_approval_plan
    if raw_plan is None:
        yield format_event(
            "error",
            {"message": "Trace was pending_approval but no plan was persisted."},
        )
        return
    try:
        plan = Plan.model_validate(raw_plan)
    except PydanticValidationError as e:
        yield format_event(
            "error", {"message": f"Persisted plan failed re-validation: {e}"}
        )
        return

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
                item = await asyncio.wait_for(
                    queue.get(), timeout=_SSE_KEEPALIVE_SECONDS
                )
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
                trace.steps = (
                    list(trace.steps) + [decision_step_dict] + new_step_dicts
                )
                trace.status = payload.status
                trace.response_text = payload.response_text
                trace.awaiting_approval_plan = None
                trace.completed_at = datetime.now(UTC)
                if payload.usage is not None:
                    trace.tokens_input += payload.usage.input_tokens
                    trace.tokens_output += payload.usage.output_tokens
                    trace.tokens_cache_creation += payload.usage.cache_creation_tokens
                    trace.tokens_cache_read += payload.usage.cache_read_tokens
                    trace.cost_usd = round(
                        trace.cost_usd + payload.usage.cost_usd, 6
                    )
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
) -> StreamingResponse:
    """SSE variant of the approve endpoint. Streams the resumed execution after
    approval; emits a single `step`+`done` pair on cancel."""
    return StreamingResponse(
        _stream_agent_approve(
            trace_id=trace_id,
            approved=body.approved,
            reason=body.reason,
            session=session,
            llm=llm,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/traces/{trace_id}")
async def get_trace(
    trace_id: str, session: AsyncSession = Depends(get_session)
) -> dict:
    result = await session.execute(select(Trace).where(Trace.id == trace_id))
    trace = result.scalar_one_or_none()
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id!r} not found")
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
