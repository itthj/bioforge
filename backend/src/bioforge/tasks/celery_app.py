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
from typing import Any

from celery import Celery

from bioforge.config import settings

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
