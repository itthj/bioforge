"""Celery application + task definitions for the optional worker pool.

This module is only imported when BIOFORGE_TASK_QUEUE=celery is set. The
worker process (`celery -A bioforge.tasks.celery_app worker`) imports this
file to register the task functions; the agent process imports it through
CeleryTaskQueue.

We define one generic dispatcher task that takes (tool_name, tool_input)
and routes through the registry. This keeps the worker code small and
avoids duplicating tool wiring — every tool registered in the agent process
is automatically usable from the worker without changes.

Result backend: Redis URL configured via BIOFORGE_REDIS_URL. Default
redis://redis:6379/0 matches the docker-compose service name.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from celery import Celery

from bioforge.config import settings


def _run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run an async coroutine to completion from Celery's synchronous task body.

    A real worker process has no running event loop, so ``asyncio.run`` works directly. Under
    ``task_always_eager=True`` (the hermetic test path) the task executes INSIDE the caller's
    running loop, where ``asyncio.run`` raises -- so we run the coroutine on a dedicated thread
    with its own loop. Either way the coroutine builds its own DB engine, so the fresh loop is
    safe.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


REDIS_URL = settings.redis_url

celery_app = Celery(
    "bioforge",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

# Tunables. Defaults follow Celery 5.x best practices for long-running tasks; the time
# limits come from Settings (BIOFORGE_CELERY_TASK_*_TIME_LIMIT).
celery_app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,  # one task at a time per worker — fair for slow tools
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_time_limit=settings.celery_task_time_limit,
    task_soft_time_limit=settings.celery_task_soft_time_limit,
)


@celery_app.task(name="bioforge.tasks.run_tool")
def run_tool_task(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """Generic worker task: dispatch (tool_name, input) through the registry.

    Importing tools.registry pulls in the @register_tool side-effect chain,
    so every tool registered for the agent process is automatically callable
    here too. The result is serialized via Pydantic's model_dump() so it
    survives Celery's JSON round-trip.
    """
    # Import inside the task body so the worker only loads the tool registry
    # when it picks up its first task — keeps worker startup fast.
    from bioforge.tools.registry import execute_tool

    # Celery tasks run in a synchronous worker context; bridge to asyncio.
    result = asyncio.run(execute_tool(tool_name, tool_input))
    return result.model_dump()


@celery_app.task(name="bioforge.tasks.run_agent_job")
def run_agent_job_task(
    trace_id: str,
    goal: str,
    project_id: str,
    autonomy: str = "auto",
) -> dict[str, str]:
    """Per-run durable job: execute a queued agent run to completion in the worker.

    The whole ``run_agent`` loop runs here (not just one tool) so a long run survives an API
    restart or client disconnect -- the durability win of the phase. The trace row already exists
    (created ``queued`` + committed by the enqueueing request); we load it, stream each step into
    the DB, and write the terminal state. Returns a small JSON-safe summary for the result backend.
    """
    # Lazy import so the worker only loads the agent stack when it picks up its first run.
    from bioforge.agent.jobs import run_agent_job_async

    return _run_async(run_agent_job_async(trace_id=trace_id, goal=goal, project_id=project_id, autonomy=autonomy))


@celery_app.task(name="bioforge.tasks.run_pipeline_job")
def run_pipeline_job_task(job_id: str) -> dict[str, str]:
    """Execute an nf-core pipeline job end-to-end in the worker.

    The PipelineJob row must already exist with status='queued'. The task loads it,
    runs the pipeline, persists each event back to the row, and writes the terminal
    status. Returns a small JSON-safe summary for the result backend.
    """
    from bioforge.pipelines.jobs import run_pipeline_job_async

    return _run_async(run_pipeline_job_async(job_id=job_id))


@celery_app.task(name="bioforge.tasks.resume_agent_job")
def resume_agent_job_task(trace_id: str, plan: dict[str, Any], step_idx_start: int) -> dict[str, str]:
    """Durable resume of an APPROVED run (P2b). The whole post-approval executor/critic loop runs
    in the worker, just like the initial run, so a review-mode run is durable too. The enqueueing
    request persisted the decision step + approved plan and flipped the trace to running; we
    continue from step_idx_start. `plan` is the JSON-safe approved plan dict."""
    from bioforge.agent.jobs import resume_agent_job_async

    return _run_async(resume_agent_job_async(trace_id=trace_id, plan=plan, step_idx_start=step_idx_start))
