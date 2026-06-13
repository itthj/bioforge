"""nf-core pipeline API (Limitation #5).

Endpoints
---------
POST   /pipelines              Submit a new pipeline job (queued or inline).
GET    /pipelines?project_id=x List pipeline jobs for a project.
GET    /pipelines/{id}         Full job detail + events.
GET    /pipelines/{id}/stream  SSE progress stream (poll-while-write, same pattern as agent stream).
DELETE /pipelines/{id}         Cancel a running job.

Gate
----
`BIOFORGE_NEXTFLOW_ENABLED=true` is required at execution time (checked in runner.py).  The
API routes themselves are always available so the frontend can list/poll without the binary.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bioforge.api.auth import get_current_user, require_project_access
from bioforge.api.sse import format_event, format_keepalive
from bioforge.config import settings
from bioforge.db.engine import get_session
from bioforge.db.models import PipelineJob, User
from bioforge.pipelines.runner import SUPPORTED_PIPELINES

router = APIRouter()

# How often the SSE loop re-reads PipelineJob.events while the job is running.
_POLL_SECONDS = 0.5
_KEEPALIVE_SECONDS = 15.0
_STALE_MARGIN = 60.0  # seconds past pipeline_work hard limit before we declare stale


class PipelineSubmitRequest(BaseModel):
    project_id: str
    pipeline: str = Field(..., examples=["nf-core/rnaseq"])
    revision: str | None = Field(
        default=None,
        description="Pinned nf-core version tag. Defaults to the catalogue value for this pipeline.",
    )
    profile: str = Field(default="test", description="Comma-separated nextflow profiles, e.g. 'test,docker'.")
    samplesheet: str = Field(..., description="Full CSV content of the nf-core samplesheet.")
    params: dict[str, str] | None = Field(default=None, description="Extra --key value pairs forwarded to nextflow.")


class PipelineJobOut(BaseModel):
    id: str
    project_id: str
    pipeline: str
    revision: str
    profile: str
    status: str
    events: list
    error: str | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None

    @classmethod
    def from_orm(cls, job: PipelineJob) -> PipelineJobOut:
        return cls(
            id=job.id,
            project_id=job.project_id,
            pipeline=job.pipeline,
            revision=job.revision,
            profile=job.profile,
            status=job.status,
            events=job.events or [],
            error=job.error,
            created_at=job.created_at.isoformat(),
            started_at=job.started_at.isoformat() if job.started_at else None,
            completed_at=job.completed_at.isoformat() if job.completed_at else None,
        )


def _is_terminal(status: str) -> bool:
    return status in ("completed", "failed", "cancelled")


async def _refetch_job(session: AsyncSession, job_id: str) -> PipelineJob | None:
    await session.rollback()
    result = await session.execute(
        select(PipelineJob).where(PipelineJob.id == job_id).execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


@router.post("/pipelines", response_model=PipelineJobOut, status_code=202)
async def submit_pipeline(
    body: PipelineSubmitRequest,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> PipelineJobOut:
    """Submit an nf-core pipeline job.

    The job is created in `queued` status and, when BIOFORGE_TASK_QUEUE=celery, dispatched to
    a Celery worker. In the default inline mode it runs in the current request handler
    synchronously (blocking -- only suitable for the `test` profile's fast smoke test).
    """
    await require_project_access(session, body.project_id, current_user)

    if body.pipeline not in SUPPORTED_PIPELINES:
        supported = ", ".join(sorted(SUPPORTED_PIPELINES))
        raise HTTPException(status_code=422, detail=f"Unsupported pipeline. Supported: {supported}")

    revision = body.revision or SUPPORTED_PIPELINES[body.pipeline]

    import json as _json

    job = PipelineJob(
        project_id=body.project_id,
        pipeline=body.pipeline,
        revision=revision,
        profile=body.profile,
        samplesheet=body.samplesheet,
        params_json=_json.dumps(body.params) if body.params else None,
        status="queued",
    )
    session.add(job)
    await session.flush()
    await session.commit()
    await session.refresh(job)

    if settings.task_queue == "celery":
        from bioforge.tasks.celery_app import run_pipeline_job_task

        celery_result = run_pipeline_job_task.delay(job.id)
        job.celery_task_id = celery_result.id
        await session.commit()
    else:
        # Inline mode: run directly through the current session (no separate DB engine).
        # Blocks the server -- only suitable for the fast `test` profile / CI smoke tests.
        import json as _json2
        from datetime import UTC, datetime
        from pathlib import Path

        from bioforge.pipelines.runner import run_pipeline

        extra = _json2.loads(job.params_json) if job.params_json else None
        job.status = "running"
        job.started_at = datetime.now(UTC)
        await session.commit()

        collected: list = []

        def _on_event(ev):  # sync callback; run_pipeline awaits it only when coro
            collected.append(ev.to_dict())

        try:
            final_status, error = await run_pipeline(
                job_id=job.id,
                pipeline=job.pipeline,
                revision=job.revision,
                profile=job.profile,
                samplesheet_csv=job.samplesheet or "",
                work_dir=Path(settings.pipeline_work_dir) / job.id,
                extra_params=extra,
                on_event=_on_event,
            )
        except Exception as exc:  # noqa: BLE001
            final_status = "failed"
            error = str(exc)

        await session.rollback()
        job = await session.get(PipelineJob, job.id)  # type: ignore[assignment]
        job.events = collected  # type: ignore[union-attr]
        job.status = final_status
        job.completed_at = datetime.now(UTC)
        if error:
            job.error = error
        await session.commit()
        await session.refresh(job)

    return PipelineJobOut.from_orm(job)


@router.get("/pipelines", response_model=list[PipelineJobOut])
async def list_pipelines(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[PipelineJobOut]:
    await require_project_access(session, project_id, current_user)
    result = await session.execute(
        select(PipelineJob)
        .where(PipelineJob.project_id == project_id)
        .order_by(PipelineJob.created_at.desc())
        .limit(50)
    )
    return [PipelineJobOut.from_orm(j) for j in result.scalars().all()]


@router.get("/pipelines/{job_id}", response_model=PipelineJobOut)
async def get_pipeline(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> PipelineJobOut:
    job = await session.get(PipelineJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Pipeline job not found")
    await require_project_access(session, job.project_id, current_user)
    return PipelineJobOut.from_orm(job)


@router.get("/pipelines/{job_id}/stream")
async def stream_pipeline(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    """SSE stream of pipeline events. Catch-up plays everything already persisted, then
    polls for new events until the job reaches a terminal status."""
    job = await session.get(PipelineJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Pipeline job not found")
    await require_project_access(session, job.project_id, current_user)

    return StreamingResponse(
        _stream_pipeline_progress(job_id=job_id, session=session),
        media_type="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


async def _stream_pipeline_progress(*, job_id: str, session: AsyncSession) -> AsyncIterator[str]:
    job = await _refetch_job(session, job_id)
    if job is None:
        yield format_event("error", {"message": f"Pipeline job {job_id!r} not found"})
        return

    emitted = 0
    for ev in job.events or []:
        yield format_event("event", ev)
        emitted += 1

    if _is_terminal(job.status):
        yield format_event("done", {"status": job.status, "error": job.error})
        return

    start = time.monotonic()
    last_keepalive = start
    max_wall = settings.celery_task_time_limit + _STALE_MARGIN

    while True:
        await asyncio.sleep(_POLL_SECONDS)
        job = await _refetch_job(session, job_id)
        if job is None:
            yield format_event("error", {"message": f"Pipeline job {job_id!r} disappeared mid-stream"})
            return

        new_events = (job.events or [])[emitted:]
        if new_events:
            for ev in new_events:
                yield format_event("event", ev)
            emitted += len(new_events)

        if _is_terminal(job.status):
            yield format_event("done", {"status": job.status, "error": job.error})
            return

        now = time.monotonic()
        if now - last_keepalive > _KEEPALIVE_SECONDS:
            yield format_keepalive()
            last_keepalive = now

        if now - start > max_wall:
            yield format_event(
                "error",
                {"message": (f"Job still not terminal after {max_wall:.0f}s. Last known status: {job.status!r}.")},
            )
            return


@router.delete("/pipelines/{job_id}", status_code=204)
async def cancel_pipeline(
    job_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    job = await session.get(PipelineJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Pipeline job not found")
    await require_project_access(session, job.project_id, current_user)

    if _is_terminal(job.status):
        return  # already done, nothing to cancel

    if job.celery_task_id and settings.task_queue == "celery":
        from bioforge.tasks.celery_app import celery_app

        celery_app.control.revoke(job.celery_task_id, terminate=True)

    job.status = "cancelled"
    await session.commit()


@router.get("/pipelines/catalogue/supported")
async def list_supported_pipelines() -> dict[str, str]:
    """Return the catalogue of supported nf-core pipelines and their pinned default revisions."""
    return SUPPORTED_PIPELINES
