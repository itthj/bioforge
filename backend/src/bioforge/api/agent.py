from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bioforge.agent import AgentResult, Plan, resume_agent, run_agent
from bioforge.agent.llm import LLM
from bioforge.constants import DEFAULT_PROJECT_ID
from bioforge.db.engine import get_session
from bioforge.db.models import Trace

router = APIRouter()


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
        completed_at=datetime.now(timezone.utc),
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
    result = await run_agent(body.goal, project_id=body.project_id, llm=llm)
    trace = await _persist_new_trace(session, result)
    return _trace_to_response(trace, result)


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
        trace.completed_at = datetime.now(timezone.utc)
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
    trace.completed_at = datetime.now(timezone.utc)
    if new_result.usage is not None:
        trace.tokens_input += new_result.usage.input_tokens
        trace.tokens_output += new_result.usage.output_tokens
        trace.tokens_cache_creation += new_result.usage.cache_creation_tokens
        trace.tokens_cache_read += new_result.usage.cache_read_tokens
        trace.cost_usd = round(trace.cost_usd + new_result.usage.cost_usd, 6)
    await session.flush()

    return _trace_to_response(trace, new_result)


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
