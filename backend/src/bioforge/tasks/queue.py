"""TaskQueue interface + Inline / Celery implementations.

Design goal: dropping Celery in or out of the deployment is a config change,
not a code change. Tool handlers don't import Celery; they call
`task_queue.run(tool_name, input_dict)` and get back a handle that gives
them the same result either way.

The InlineTaskQueue is the default — it runs tasks in the calling process,
keeping the legacy behavior for single-user local. CeleryTaskQueue enqueues
via the Celery app in `celery_app.py` and polls the result.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from bioforge.tools.registry import execute_tool


class TaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    success = "success"
    failure = "failure"


@dataclass
class TaskHandle:
    """Result handle returned from `TaskQueue.run`.

    For the Inline queue, the result is already populated when the handle
    returns. For Celery, the handle holds the Celery AsyncResult id and the
    caller awaits via `result()`.
    """

    task_id: str
    status: TaskStatus
    backend: str
    result: dict[str, Any] | None = None
    error: str | None = None


@runtime_checkable
class TaskQueue(Protocol):
    """The contract all task-queue implementations satisfy."""

    backend_name: str

    async def submit(self, tool_name: str, tool_input: dict[str, Any]) -> TaskHandle: ...
    async def result(self, handle: TaskHandle, timeout_seconds: float = 600.0) -> TaskHandle: ...


# --- Inline (default) ------------------------------------------------------------


class InlineTaskQueue:
    """Runs tasks synchronously in the current process. Zero infrastructure.

    This is the default for single-user local deployments. It preserves the
    pre-task-queue behavior bit-for-bit: a tool call is a regular Python
    `await execute_tool(...)` and the handle is already-completed when
    submit() returns.
    """

    backend_name = "inline"

    async def submit(self, tool_name: str, tool_input: dict[str, Any]) -> TaskHandle:
        try:
            result = await execute_tool(tool_name, tool_input)
            return TaskHandle(
                task_id=f"inline-{tool_name}",
                status=TaskStatus.success,
                backend=self.backend_name,
                result=result.model_dump(),
            )
        except Exception as e:  # noqa: BLE001
            return TaskHandle(
                task_id=f"inline-{tool_name}",
                status=TaskStatus.failure,
                backend=self.backend_name,
                error=f"{type(e).__name__}: {e}",
            )

    async def result(self, handle: TaskHandle, timeout_seconds: float = 600.0) -> TaskHandle:
        # Inline submit() already populated the result.
        return handle


# --- Celery --------------------------------------------------------------------


class CeleryTaskQueue:
    """Enqueues tasks against the Celery worker pool defined in celery_app.py.

    The Celery app + Redis broker must be running; otherwise submit() raises
    an actionable error rather than silently hanging.
    """

    backend_name = "celery"

    def __init__(self, app: Any | None = None) -> None:
        # Import lazily so the Inline queue path doesn't pay the cost of
        # importing Celery + Redis when they're not configured.
        if app is None:
            from bioforge.tasks.celery_app import celery_app as _default_app

            app = _default_app
        self._app = app

    async def submit(self, tool_name: str, tool_input: dict[str, Any]) -> TaskHandle:
        # The dispatcher task name in celery_app.py.
        async_result = self._app.send_task(
            "bioforge.tasks.run_tool",
            args=[tool_name, tool_input],
        )
        return TaskHandle(
            task_id=async_result.id,
            status=TaskStatus.pending,
            backend=self.backend_name,
        )

    async def result(self, handle: TaskHandle, timeout_seconds: float = 600.0) -> TaskHandle:
        # Run the (blocking) Celery .get() in a worker thread so we don't
        # tie up the asyncio event loop.
        import asyncio

        from celery.result import AsyncResult

        def _wait() -> tuple[bool, Any]:
            r = AsyncResult(handle.task_id, app=self._app)
            try:
                value = r.get(timeout=timeout_seconds, propagate=False)
                return r.successful(), value
            except Exception as e:  # noqa: BLE001
                return False, f"{type(e).__name__}: {e}"

        ok, value = await asyncio.to_thread(_wait)
        if ok:
            return TaskHandle(
                task_id=handle.task_id,
                status=TaskStatus.success,
                backend=self.backend_name,
                result=value if isinstance(value, dict) else {"value": value},
            )
        return TaskHandle(
            task_id=handle.task_id,
            status=TaskStatus.failure,
            backend=self.backend_name,
            error=str(value),
        )


# --- Selection -----------------------------------------------------------------


_QUEUE_SINGLETON: TaskQueue | None = None


def get_task_queue() -> TaskQueue:
    """Return the configured task queue. Selection is env-driven so a deployment
    can flip between Inline and Celery without code changes.

    BIOFORGE_TASK_QUEUE values:
      - 'inline' (default): InlineTaskQueue, no broker needed.
      - 'celery': CeleryTaskQueue, requires Redis + worker.
    """
    global _QUEUE_SINGLETON
    if _QUEUE_SINGLETON is not None:
        return _QUEUE_SINGLETON
    backend = os.environ.get("BIOFORGE_TASK_QUEUE", "inline").strip().lower()
    if backend == "celery":
        _QUEUE_SINGLETON = CeleryTaskQueue()
    else:
        _QUEUE_SINGLETON = InlineTaskQueue()
    return _QUEUE_SINGLETON


def reset_task_queue() -> None:
    """Reset the singleton — used by tests that need to swap backends."""
    global _QUEUE_SINGLETON
    _QUEUE_SINGLETON = None
