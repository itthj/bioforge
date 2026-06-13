"""Async job body for pipeline runs (called from the Celery task).

The Celery task is synchronous; it calls _run_async(run_pipeline_job_async(...)).
This module contains the async implementation so it can be tested with pytest-asyncio
without standing up a real Celery worker.

The worker builds its own engine + sessionmaker from settings.db_url (identical to the
agent/jobs.py pattern) so it doesn't share connections with the API process.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bioforge.config import settings
from bioforge.db.models import PipelineJob
from bioforge.pipelines.runner import PipelineEvent, run_pipeline


async def run_pipeline_job_async(*, job_id: str) -> dict[str, str]:
    """Load PipelineJob by id, execute it, persist events, write terminal status."""
    engine = create_async_engine(settings.db_url, echo=False, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with maker() as session:
            job = (await session.execute(select(PipelineJob).where(PipelineJob.id == job_id))).scalar_one_or_none()
            if job is None:
                return {"status": "error", "error": f"PipelineJob {job_id!r} not found"}

            job.status = "running"
            job.started_at = datetime.now(UTC)
            await session.commit()

        async def persist_event(event: PipelineEvent) -> None:
            async with maker() as session:
                job = (
                    await session.execute(
                        select(PipelineJob).where(PipelineJob.id == job_id).execution_options(populate_existing=True)
                    )
                ).scalar_one()
                current_events: list = list(job.events or [])
                current_events.append(event.to_dict())
                job.events = current_events
                await session.commit()

        work_dir = Path(settings.pipeline_work_dir) / job_id

        # Re-read the job to get pipeline params (done outside maker context above).
        async with maker() as session:
            job = (await session.execute(select(PipelineJob).where(PipelineJob.id == job_id))).scalar_one()
            pipeline = job.pipeline
            revision = job.revision
            profile = job.profile
            samplesheet = job.samplesheet or ""
            extra_params = json.loads(job.params_json) if job.params_json else None

        final_status, error = await run_pipeline(
            job_id=job_id,
            pipeline=pipeline,
            revision=revision,
            profile=profile,
            samplesheet_csv=samplesheet,
            work_dir=work_dir,
            extra_params=extra_params,
            on_event=persist_event,
        )

        async with maker() as session:
            job = (await session.execute(select(PipelineJob).where(PipelineJob.id == job_id))).scalar_one()
            job.status = final_status
            job.completed_at = datetime.now(UTC)
            if error:
                job.error = error
            await session.commit()

        return {"status": final_status, "job_id": job_id}

    finally:
        await engine.dispose()
