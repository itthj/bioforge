"""Nextflow workflow engine adapter (Phase 5.5).

Translates a list of `WorkflowStep` objects into a generated `.nf` script,
shells out to `nextflow run`, and tails the run's trace file to emit
`WorkflowEvent`s. The actual `nextflow` binary is NOT bundled — install it
separately (https://www.nextflow.io/) and point at it via `nextflow_bin`.

# Steps must be command-mode

Nextflow processes shell out; they cannot host Python `async` callables.
NextflowEngine therefore only accepts `WorkflowStep`s where `command` is
set. A step with only `handler` would have nothing to run inside the
Nextflow process block and the engine raises a clear ValueError at submit
time rather than producing a broken .nf script.

# Test discipline

The engine is testable WITHOUT a real `nextflow` binary by injecting a
subprocess runner. `_run_nextflow_subprocess` is the seam — tests patch
it to assert on the generated script + simulate trace-file evolution.
The shipped default uses `asyncio.create_subprocess_exec`.

# Feature flag

`BIOFORGE_NEXTFLOW_ENABLED=true` gates the integration. Defaults to False
so accidental swaps from LocalWorkflowEngine surface as an explicit error
rather than a subprocess crash on a missing binary.

# Outputs convention

A Nextflow process's structured output lands as a JSON file at
`{work_dir}/{step_name}.json` (the step's `command` is responsible for
writing it). The engine reads these post-completion and populates
`step_outputs`. Steps that don't write a JSON file produce `{}` outputs
— success/failure is tracked through trace exit codes regardless.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import signal
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bioforge.workflows.engine import (
    WorkflowEvent,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
)

# Trace file column header Nextflow writes when `-with-trace` is set.
# We pin the columns we depend on; future Nextflow versions may add more.
_TRACE_HEADER_COLS_MIN = ("task_id", "name", "status", "exit")


@dataclass
class _SubprocessResult:
    """Outcome of one `nextflow run` invocation. Surfaced to the engine for
    state tracking; tests build these inline."""

    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""


@dataclass
class _RunState:
    """Per-run bookkeeping. The engine holds one per active run_id."""

    run_id: str
    status: WorkflowStatus
    work_dir: Path
    nf_script_path: Path
    trace_path: Path
    steps: list[WorkflowStep]
    started_at: float | None = None
    finished_at: float | None = None
    error_message: str | None = None
    step_outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    subprocess_task: asyncio.Task[_SubprocessResult] | None = None
    queue: asyncio.Queue[WorkflowEvent | None] | None = None
    process: asyncio.subprocess.Process | None = None


# Type of the subprocess runner. Tests inject a fake.
SubprocessRunner = Callable[[list[str], Path], "asyncio.Future[_SubprocessResult]"]


async def _default_subprocess_runner(argv: list[str], work_dir: Path) -> _SubprocessResult:
    """Default runner: shell out to the real `nextflow` binary via asyncio.

    Captures stdout + stderr (Nextflow itself writes the trace + log to disk;
    we mostly use stdout/stderr for diagnostics on failure).
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(work_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return _SubprocessResult(returncode=proc.returncode or 0, stdout=out, stderr=err)


# --- Script generation ---------------------------------------------------------------


_NAME_SAFE_RE = re.compile(r"[^A-Za-z0-9_]+")


def _sanitize_process_name(name: str) -> str:
    """Nextflow process names must be valid Groovy identifiers."""
    sanitized = _NAME_SAFE_RE.sub("_", name)
    if not sanitized:
        sanitized = "step"
    if not sanitized[0].isalpha() and sanitized[0] != "_":
        sanitized = "p_" + sanitized
    return sanitized


def generate_nf_script(steps: list[WorkflowStep]) -> str:
    """Generate a DSL2 Nextflow script from `steps`.

    Each step becomes a process block. Dependencies are honored via the
    workflow block's call order: a step that depends on prerequisites only
    fires after they emit a value. We use simple value channels (no file
    fanout) since BioForge workflows today are at the granularity of
    "one external API call per step".

    Tests pin the output verbatim, so this function must be deterministic —
    no timestamps or random IDs in the generated text.
    """
    if not steps:
        raise ValueError("Cannot generate a Nextflow script from an empty step list.")
    for s in steps:
        if not s.command:
            raise ValueError(
                f"NextflowEngine step {s.name!r} has no `command`. Nextflow processes shell out; "
                "Python handlers cannot run inside a process block."
            )

    lines: list[str] = ["nextflow.enable.dsl=2", ""]

    # Process declarations.
    for s in steps:
        proc_name = _sanitize_process_name(s.name)
        # Build the input declaration. For now, no upstream channel inputs —
        # depends_on is honored via workflow-block ordering. Outputs are the
        # JSON file convention.
        lines.append(f"process {proc_name} {{")
        lines.append('    publishDir ".", mode: "copy"')
        lines.append("")
        lines.append("    output:")
        lines.append(f'    path "{s.name}.json" optional true')
        lines.append("")
        lines.append("    script:")
        lines.append('    """')
        # Indent the command body so the heredoc is consistent.
        for cmd_line in s.command.splitlines() or [s.command]:
            lines.append(f"    {cmd_line}")
        lines.append('    """')
        lines.append("}")
        lines.append("")

    # Workflow block: call each process, respecting dependencies via topological order.
    ordered = _topological_sort_safely(steps)
    lines.append("workflow {")
    for s in ordered:
        proc_name = _sanitize_process_name(s.name)
        if s.depends_on:
            # Express dependency by referencing prior outputs.
            deps = ", ".join(_sanitize_process_name(d) + ".out" for d in s.depends_on)
            lines.append(f"    {proc_name}({deps})  // depends_on: {s.depends_on}")
        else:
            lines.append(f"    {proc_name}()")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def _topological_sort_safely(steps: list[WorkflowStep]) -> list[WorkflowStep]:
    """Topological sort with cycle / unknown-dep detection. Mirrors the
    engine.py version but kept here so this module doesn't import a private
    helper."""
    by_name = {s.name: s for s in steps}
    visited: set[str] = set()
    in_progress: set[str] = set()
    ordered: list[WorkflowStep] = []

    def visit(n: str) -> None:
        if n in visited:
            return
        if n in in_progress:
            raise ValueError(f"Cycle in workflow dependencies involving {n!r}")
        if n not in by_name:
            raise ValueError(f"Unknown dependency {n!r}")
        in_progress.add(n)
        for dep in by_name[n].depends_on:
            visit(dep)
        in_progress.discard(n)
        visited.add(n)
        ordered.append(by_name[n])

    for s in steps:
        visit(s.name)
    return ordered


# --- Trace parsing ------------------------------------------------------------------


@dataclass
class _TraceRow:
    task_id: str
    name: str
    status: str
    exit: str


def parse_trace_file(path: Path) -> list[_TraceRow]:
    """Read a Nextflow `-with-trace` file and return one _TraceRow per task.

    The trace file is tab-separated with a header. We extract only the four
    columns we need; everything else is ignored. Returns an empty list if
    the file doesn't exist or has only a header.
    """
    if not path.exists():
        return []
    rows: list[_TraceRow] = []
    with path.open("r", encoding="utf-8") as f:
        header_line = f.readline().strip()
        if not header_line:
            return []
        cols = header_line.split("\t")
        try:
            idx = {c: cols.index(c) for c in _TRACE_HEADER_COLS_MIN}
        except ValueError as e:
            raise ValueError(f"Trace file at {path} missing expected column: {e}") from e

        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < len(cols):
                continue
            rows.append(
                _TraceRow(
                    task_id=parts[idx["task_id"]],
                    name=parts[idx["name"]],
                    status=parts[idx["status"]],
                    exit=parts[idx["exit"]],
                )
            )
    return rows


# --- The engine itself --------------------------------------------------------------


class NextflowEngine:
    """Nextflow-backed WorkflowEngine implementation."""

    def __init__(
        self,
        *,
        nextflow_bin: str = "nextflow",
        work_dir: str | Path = "./.nextflow_work",
        subprocess_runner: SubprocessRunner | None = None,
        feature_flag_env: str = "BIOFORGE_NEXTFLOW_ENABLED",
    ) -> None:
        self.nextflow_bin = nextflow_bin
        self.work_dir_root = Path(work_dir)
        self._subprocess_runner = subprocess_runner or _default_subprocess_runner
        self._feature_flag_env = feature_flag_env
        self._runs: dict[str, _RunState] = {}

    def _require_enabled(self) -> None:
        """Refuse to do anything unless the feature flag is set. Prevents an
        accidental engine swap from blowing up on a missing `nextflow` binary."""
        val = os.environ.get(self._feature_flag_env, "").lower()
        if val not in ("1", "true", "yes"):
            raise RuntimeError(
                f"NextflowEngine is gated behind {self._feature_flag_env}=true. "
                "Install Nextflow (https://www.nextflow.io/) and opt in explicitly. "
                "Until then, LocalWorkflowEngine handles in-process workflows."
            )
        # Best-effort check that the binary exists (skipped for tests that
        # inject a fake subprocess_runner — in that case the env flag alone is enough).
        if self._subprocess_runner is _default_subprocess_runner and shutil.which(self.nextflow_bin) is None:
            raise RuntimeError(
                f"`{self.nextflow_bin}` not found on PATH. Install Nextflow or set nextflow_bin to its absolute path."
            )

    # --- Protocol surface ------------------------------------------------------

    async def submit(self, steps: list[WorkflowStep]) -> WorkflowRun:
        self._require_enabled()

        for s in steps:
            if not s.command:
                raise ValueError(
                    f"Step {s.name!r} has no `command`. NextflowEngine cannot host Python "
                    "handlers — convert the step body to a shell command (e.g. a "
                    "`python -m <module>` invocation)."
                )

        run_id = uuid.uuid4().hex
        work_dir = self.work_dir_root / run_id
        work_dir.mkdir(parents=True, exist_ok=True)
        nf_script_path = work_dir / "main.nf"
        trace_path = work_dir / "trace.txt"

        nf_script_path.write_text(generate_nf_script(steps), encoding="utf-8")

        state = _RunState(
            run_id=run_id,
            status=WorkflowStatus.submitted,
            work_dir=work_dir,
            nf_script_path=nf_script_path,
            trace_path=trace_path,
            steps=steps,
            queue=asyncio.Queue(),
        )
        self._runs[run_id] = state

        # Kick off the subprocess in the background; stream_progress will tail
        # the trace and yield events as they appear.
        state.subprocess_task = asyncio.create_task(self._run_workflow(state))
        return WorkflowRun(run_id=run_id, status=WorkflowStatus.submitted)

    async def stream_progress(self, run_id: str) -> AsyncIterator[WorkflowEvent]:
        state = self._runs.get(run_id)
        if state is None:
            raise KeyError(f"Unknown run_id {run_id!r}")
        assert state.queue is not None
        while True:
            event = await state.queue.get()
            if event is None:
                return
            yield event

    async def cancel(self, run_id: str) -> None:
        state = self._runs.get(run_id)
        if state is None:
            raise KeyError(f"Unknown run_id {run_id!r}")
        state.cancel_event.set()
        # Try to SIGINT the running subprocess if we have a handle. The
        # default subprocess_runner only returns a result; tests that need
        # mid-run cancellation expose a process handle via the subprocess_task.
        if state.process is not None:
            with contextlib.suppress(ProcessLookupError):
                state.process.send_signal(signal.SIGINT)
        if state.subprocess_task is not None and not state.subprocess_task.done():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await state.subprocess_task

    async def get_run(self, run_id: str) -> WorkflowRun:
        state = self._runs.get(run_id)
        if state is None:
            raise KeyError(f"Unknown run_id {run_id!r}")
        return WorkflowRun(
            run_id=run_id,
            status=state.status,
            step_outputs=dict(state.step_outputs),
            error_message=state.error_message,
            started_at=state.started_at,
            finished_at=state.finished_at,
        )

    # --- Internal --------------------------------------------------------------

    async def _run_workflow(self, state: _RunState) -> _SubprocessResult:
        """Drive one Nextflow run end-to-end: invoke subprocess, tail trace,
        collect outputs. The queue accumulates events for stream_progress."""
        assert state.queue is not None
        queue = state.queue

        state.status = WorkflowStatus.running
        state.started_at = time.time()
        await queue.put(WorkflowEvent(type="run_started", run_id=state.run_id))

        argv = [
            self.nextflow_bin,
            "run",
            str(state.nf_script_path),
            "-with-trace",
            str(state.trace_path),
            "-name",
            state.run_id,
        ]

        # Tail the trace file in parallel with the subprocess so progress
        # events arrive as steps complete rather than at the end.
        runner_task: asyncio.Task[_SubprocessResult] = asyncio.create_task(
            self._subprocess_runner(argv, state.work_dir)  # type: ignore[arg-type]
        )
        tail_task = asyncio.create_task(self._tail_trace(state, runner_task))

        try:
            result = await runner_task
        except Exception as e:  # noqa: BLE001
            state.status = WorkflowStatus.failed
            state.error_message = f"{type(e).__name__}: {e}"
            state.finished_at = time.time()
            await queue.put(
                WorkflowEvent(type="run_failed", run_id=state.run_id, payload={"error": state.error_message})
            )
            await queue.put(None)
            tail_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tail_task
            return _SubprocessResult(returncode=-1, stderr=str(e).encode())

        # Wait for the tail to drain whatever the trace file has post-completion.
        with contextlib.suppress(asyncio.CancelledError):
            await tail_task

        # Final pass on the trace + outputs.
        self._collect_outputs(state)
        cancelled = state.cancel_event.is_set()
        if cancelled:
            state.status = WorkflowStatus.cancelled
            await queue.put(WorkflowEvent(type="run_cancelled", run_id=state.run_id))
        elif result.returncode == 0:
            state.status = WorkflowStatus.completed
            await queue.put(WorkflowEvent(type="run_completed", run_id=state.run_id))
        else:
            state.status = WorkflowStatus.failed
            stderr_tail = result.stderr.decode(errors="replace")[-500:]
            state.error_message = f"nextflow exit {result.returncode}: {stderr_tail!r}"
            await queue.put(
                WorkflowEvent(type="run_failed", run_id=state.run_id, payload={"error": state.error_message})
            )

        state.finished_at = time.time()
        await queue.put(None)
        return result

    async def _tail_trace(self, state: _RunState, runner_task: asyncio.Task[_SubprocessResult]) -> None:
        """Poll the trace file for new rows and emit step_started / step_completed /
        step_failed events. Stops once the subprocess has finished AND we've drained
        the file. Test-mockable because we use the same parse_trace_file the engine uses."""
        assert state.queue is not None
        seen: set[str] = set()
        poll_interval = 0.1
        while True:
            rows = parse_trace_file(state.trace_path)
            for row in rows:
                key = f"{row.task_id}:{row.status}"
                if key in seen:
                    continue
                seen.add(key)
                step_name = row.name
                if row.status in ("SUBMITTED", "RUNNING"):
                    await state.queue.put(WorkflowEvent(type="step_started", run_id=state.run_id, step_name=step_name))
                elif row.status in ("COMPLETED", "CACHED"):
                    await state.queue.put(
                        WorkflowEvent(
                            type="step_completed",
                            run_id=state.run_id,
                            step_name=step_name,
                            payload={"exit": row.exit},
                        )
                    )
                elif row.status in ("FAILED", "ABORTED"):
                    await state.queue.put(
                        WorkflowEvent(
                            type="step_failed",
                            run_id=state.run_id,
                            step_name=step_name,
                            payload={"exit": row.exit},
                        )
                    )
            if runner_task.done() and not state.cancel_event.is_set():
                # One final read to catch trailing rows the subprocess wrote between polls.
                final_rows = parse_trace_file(state.trace_path)
                if len(final_rows) <= len(rows):
                    return
                continue
            if state.cancel_event.is_set():
                return
            await asyncio.sleep(poll_interval)

    def _collect_outputs(self, state: _RunState) -> None:
        """Read `{work_dir}/{step_name}.json` for each step that wrote one. The
        agent's NextflowEngine-using tools rely on this convention."""
        for step in state.steps:
            output_path = state.work_dir / f"{step.name}.json"
            if not output_path.exists():
                continue
            try:
                with output_path.open("r", encoding="utf-8") as f:
                    state.step_outputs[step.name] = json.load(f)
            except (OSError, json.JSONDecodeError):
                # Don't kill the whole run on one unreadable output file; the
                # status was already set from the trace.
                continue
