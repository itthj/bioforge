"""Workflow engine interface + an in-process baseline implementation.

The contract is small on purpose: submit a list of steps, get back a run ID
that you can stream progress events from. Step bodies are async callables —
any tool that fits the existing tool-handler shape can be a workflow step
without modification.

The LocalWorkflowEngine baseline runs steps sequentially in the current
process and pushes events through an asyncio.Queue. It's not a replacement
for Celery/Nextflow at scale, but it does:
  - Exercise the full WorkflowEngine API end-to-end.
  - Give the agent a way to drive multi-step pipelines RIGHT NOW, without
    waiting for the Nextflow integration to land.
  - Make migrating to a real distributed engine a config swap, not a rewrite.

Future: NextflowEngine implements the same Protocol by writing a .nf script
and tailing the work directory. Tests target the Protocol, not the impl.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

StepHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class WorkflowStatus(str, Enum):
    """Lifecycle of a workflow run.

    submitted → running → (completed | failed | cancelled)

    There is no "paused" state in the local engine — cancellation is final.
    Remote engines (Nextflow, SLURM) may add intermediate states later.
    """

    submitted = "submitted"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


@dataclass
class WorkflowStep:
    """One unit of work in a workflow. `inputs` are dict-form parameters
    passed to `handler`. Outputs land in the run's `step_outputs[name]`.

    Keep step granularity coarse — a step boundary is a place where the
    engine may persist state, retry, or fan out. For BLAST against 100
    sequences, the right granularity is "one BLAST per sequence" with
    fanout, not "one big BLAST step".
    """

    name: str
    handler: StepHandler
    inputs: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)


@dataclass
class WorkflowEvent:
    """A progress event streamed back to the caller as the run advances.

    `type` is one of:
      - run_started / run_completed / run_failed / run_cancelled
      - step_started / step_completed / step_failed
    """

    type: str
    run_id: str
    step_name: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class WorkflowRun:
    """Handle returned by `submit`. `status` is mutated by the engine as the
    run progresses; callers should rely on `stream_progress` for live state
    rather than polling this field."""

    run_id: str
    status: WorkflowStatus
    step_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    error_message: str | None = None
    started_at: float | None = None
    finished_at: float | None = None


@runtime_checkable
class WorkflowEngine(Protocol):
    """The contract every engine implementation must satisfy.

    Implementations: LocalWorkflowEngine (this module — in-process),
    NextflowEngine (planned, Phase 5.5), future SLURM/k8s engines.
    """

    async def submit(self, steps: list[WorkflowStep]) -> WorkflowRun: ...
    async def stream_progress(self, run_id: str) -> AsyncIterator[WorkflowEvent]: ...
    async def cancel(self, run_id: str) -> None: ...
    async def get_run(self, run_id: str) -> WorkflowRun: ...


# --- Local baseline ------------------------------------------------------------------


class LocalWorkflowEngine:
    """In-process workflow engine.

    Runs steps sequentially, respecting `depends_on` (topological order).
    Stores all runs in memory — restart loses state. Intended for:
      - Phase 5 development before the distributed engine lands.
      - Local-only single-user mode (the project's current deployment shape).
      - Unit tests of workflow-using tools.
    """

    def __init__(self) -> None:
        self._runs: dict[str, WorkflowRun] = {}
        self._queues: dict[str, asyncio.Queue[WorkflowEvent | None]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel_flags: dict[str, asyncio.Event] = {}

    async def submit(self, steps: list[WorkflowStep]) -> WorkflowRun:
        run_id = uuid.uuid4().hex
        run = WorkflowRun(run_id=run_id, status=WorkflowStatus.submitted)
        self._runs[run_id] = run
        self._queues[run_id] = asyncio.Queue()
        self._cancel_flags[run_id] = asyncio.Event()
        self._tasks[run_id] = asyncio.create_task(self._execute(run_id, steps))
        return run

    async def stream_progress(self, run_id: str) -> AsyncIterator[WorkflowEvent]:
        queue = self._queues.get(run_id)
        if queue is None:
            raise KeyError(f"Unknown run_id {run_id!r}")
        while True:
            event = await queue.get()
            if event is None:
                # Sentinel — run is over.
                return
            yield event

    async def cancel(self, run_id: str) -> None:
        flag = self._cancel_flags.get(run_id)
        if flag is None:
            raise KeyError(f"Unknown run_id {run_id!r}")
        flag.set()
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def get_run(self, run_id: str) -> WorkflowRun:
        run = self._runs.get(run_id)
        if run is None:
            raise KeyError(f"Unknown run_id {run_id!r}")
        return run

    # --- Internal --------------------------------------------------------------------

    async def _execute(self, run_id: str, steps: list[WorkflowStep]) -> None:
        run = self._runs[run_id]
        queue = self._queues[run_id]
        cancel = self._cancel_flags[run_id]
        ordered = _topological_sort(steps)

        run.status = WorkflowStatus.running
        run.started_at = time.time()
        await queue.put(WorkflowEvent(type="run_started", run_id=run_id))

        try:
            for step in ordered:
                if cancel.is_set():
                    run.status = WorkflowStatus.cancelled
                    run.finished_at = time.time()
                    await queue.put(WorkflowEvent(type="run_cancelled", run_id=run_id))
                    return

                await queue.put(WorkflowEvent(type="step_started", run_id=run_id, step_name=step.name))
                try:
                    output = await step.handler(step.inputs)
                except Exception as e:  # noqa: BLE001
                    run.status = WorkflowStatus.failed
                    run.error_message = f"{type(e).__name__}: {e}"
                    run.finished_at = time.time()
                    await queue.put(
                        WorkflowEvent(
                            type="step_failed",
                            run_id=run_id,
                            step_name=step.name,
                            payload={"error": run.error_message},
                        )
                    )
                    await queue.put(
                        WorkflowEvent(
                            type="run_failed",
                            run_id=run_id,
                            payload={"error": run.error_message},
                        )
                    )
                    return
                run.step_outputs[step.name] = output
                await queue.put(
                    WorkflowEvent(
                        type="step_completed",
                        run_id=run_id,
                        step_name=step.name,
                        payload={"output": output},
                    )
                )

            run.status = WorkflowStatus.completed
            run.finished_at = time.time()
            await queue.put(WorkflowEvent(type="run_completed", run_id=run_id))
        finally:
            # Sentinel so the streaming consumer knows the run is over.
            await queue.put(None)


def _topological_sort(steps: list[WorkflowStep]) -> list[WorkflowStep]:
    """Sort steps so each depends_on prerequisite comes first.

    Raises ValueError on a cycle or an unknown dependency.
    """
    by_name = {s.name: s for s in steps}
    visited: set[str] = set()
    in_progress: set[str] = set()
    ordered: list[WorkflowStep] = []

    def visit(name: str) -> None:
        if name in visited:
            return
        if name in in_progress:
            raise ValueError(f"Cycle in workflow dependencies involving step {name!r}")
        if name not in by_name:
            raise ValueError(f"Unknown dependency {name!r}")
        in_progress.add(name)
        for dep in by_name[name].depends_on:
            visit(dep)
        in_progress.discard(name)
        visited.add(name)
        ordered.append(by_name[name])

    for s in steps:
        visit(s.name)
    return ordered
