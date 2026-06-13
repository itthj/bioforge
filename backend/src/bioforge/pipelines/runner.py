"""Async runner for nf-core pipeline jobs.

Builds the nextflow CLI command for a pinned nf-core pipeline, shells out, and
tails the -with-trace file to emit structured events.  The event list is written
back to PipelineJob.events via the caller's DB session so the SSE endpoint can
poll it without needing direct access to this process.

Testability
-----------
`_SubprocessRunner` is the injection seam.  Tests patch it with a fake that writes
a synthetic trace and returns immediately so no real `nextflow` binary is required.

Feature flag
------------
`BIOFORGE_NEXTFLOW_ENABLED=true` must be set at task execution time or the runner
raises `RuntimeError` before touching the file system.  This keeps the default path
(no nextflow installed) safe.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bioforge.workflows.nextflow_engine import _SubprocessResult, parse_trace_file

# Type alias matching the injection seam in NextflowEngine.
_SubprocessRunner = Callable[[list[str], Path], "asyncio.Future[_SubprocessResult]"]


# Supported nf-core pipelines with their pinned default revision.
# Revision is an immutable nf-core git tag — digest-pins the pipeline code.
SUPPORTED_PIPELINES: dict[str, str] = {
    "nf-core/rnaseq": "3.14.0",
    "nf-core/sarek": "3.4.4",
    "nf-core/atacseq": "2.1.2",
    "nf-core/ampliseq": "2.11.0",
}

_NEXTFLOW_FLAG_ENV = "BIOFORGE_NEXTFLOW_ENABLED"


def _require_nextflow_enabled() -> None:
    val = os.environ.get(_NEXTFLOW_FLAG_ENV, "").lower()
    if val not in ("1", "true", "yes"):
        raise RuntimeError(
            f"nf-core pipeline execution is gated behind {_NEXTFLOW_FLAG_ENV}=true. "
            "Install Nextflow (https://www.nextflow.io/) and set the flag to opt in."
        )


@dataclass
class PipelineEvent:
    type: str
    seq: int = 0
    step_name: str | None = None
    payload: dict[str, Any] | None = None
    ts: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "type": self.type,
            "step_name": self.step_name,
            "payload": self.payload,
            "ts": self.ts,
        }


async def _default_subprocess_runner(argv: list[str], work_dir: Path) -> _SubprocessResult:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(work_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return _SubprocessResult(returncode=proc.returncode or 0, stdout=out, stderr=err)


def build_nextflow_argv(
    *,
    pipeline: str,
    revision: str,
    profile: str,
    samplesheet_path: Path,
    outdir: Path,
    trace_path: Path,
    run_name: str,
    extra_params: dict[str, str] | None = None,
) -> list[str]:
    """Build the `nextflow run` argument list for an nf-core pipeline.

    The revision pin (`--revision`) locks the pipeline to an immutable git tag so
    runs are reproducible and containers can be pulled by digest.  Never omit it.
    """
    argv = [
        "nextflow",
        "run",
        pipeline,
        "--revision",
        revision,
        "-profile",
        profile,
        "--input",
        str(samplesheet_path),
        "--outdir",
        str(outdir),
        "-with-trace",
        str(trace_path),
        "-name",
        run_name,
    ]
    for k, v in (extra_params or {}).items():
        argv += [f"--{k}", v]
    return argv


async def run_pipeline(
    *,
    job_id: str,
    pipeline: str,
    revision: str,
    profile: str,
    samplesheet_csv: str,
    work_dir: Path,
    extra_params: dict[str, str] | None = None,
    subprocess_runner: _SubprocessRunner | None = None,
    on_event: Callable[[PipelineEvent], asyncio.Future[None] | None] | None = None,
) -> tuple[str, str | None]:
    """Run one nf-core pipeline and stream PipelineEvents.

    Args:
        job_id: Used as the nextflow run name (must be lowercase alphanum + hyphens).
        pipeline: e.g. "nf-core/rnaseq"
        revision: Pinned version tag.
        profile: Comma-separated nextflow profiles, e.g. "test,docker".
        samplesheet_csv: CSV content to write as input.
        work_dir: Root directory for this run's scratch space.
        extra_params: Extra --key value pairs forwarded to the pipeline.
        subprocess_runner: Injection seam for tests. Defaults to real asyncio subprocess.
        on_event: Async or sync callback invoked for each emitted event.

    Returns:
        (final_status, error_message | None)
    """
    _require_nextflow_enabled()

    runner = subprocess_runner or _default_subprocess_runner

    work_dir.mkdir(parents=True, exist_ok=True)
    samplesheet_path = work_dir / "samplesheet.csv"
    samplesheet_path.write_text(samplesheet_csv, encoding="utf-8")
    outdir = work_dir / "results"
    outdir.mkdir(exist_ok=True)
    trace_path = work_dir / "trace.txt"

    run_name = f"bf-{job_id[:8]}"

    argv = build_nextflow_argv(
        pipeline=pipeline,
        revision=revision,
        profile=profile,
        samplesheet_path=samplesheet_path,
        outdir=outdir,
        trace_path=trace_path,
        run_name=run_name,
        extra_params=extra_params,
    )

    seq = 0

    async def emit(event: PipelineEvent) -> None:
        nonlocal seq
        event.seq = seq
        seq += 1
        if on_event is not None:
            result = on_event(event)
            if asyncio.isfuture(result) or asyncio.iscoroutine(result):
                await result  # type: ignore[arg-type]

    await emit(PipelineEvent(type="run_started"))

    # Run the subprocess and tail the trace concurrently.
    runner_task: asyncio.Task[_SubprocessResult] = asyncio.create_task(
        runner(argv, work_dir)  # type: ignore[arg-type]
    )

    cancel_requested = asyncio.Event()
    seen_trace_keys: set[str] = set()

    async def _tail() -> None:
        poll = 0.5
        while True:
            rows = parse_trace_file(trace_path)
            for row in rows:
                key = f"{row.task_id}:{row.status}"
                if key in seen_trace_keys:
                    continue
                seen_trace_keys.add(key)
                if row.status in ("SUBMITTED", "RUNNING"):
                    await emit(PipelineEvent(type="step_started", step_name=row.name))
                elif row.status in ("COMPLETED", "CACHED"):
                    await emit(PipelineEvent(type="step_completed", step_name=row.name, payload={"exit": row.exit}))
                elif row.status in ("FAILED", "ABORTED"):
                    await emit(PipelineEvent(type="step_failed", step_name=row.name, payload={"exit": row.exit}))
            if runner_task.done():
                # Final drain after process exits.
                final_rows = parse_trace_file(trace_path)
                if len(final_rows) <= len(rows):
                    return
                continue
            if cancel_requested.is_set():
                return
            await asyncio.sleep(poll)

    tail_task = asyncio.create_task(_tail())

    try:
        result = await runner_task
    except Exception as exc:  # noqa: BLE001
        tail_task.cancel()
        await asyncio.gather(tail_task, return_exceptions=True)
        await emit(PipelineEvent(type="run_failed", payload={"error": str(exc)}))
        return "failed", str(exc)

    await asyncio.gather(tail_task, return_exceptions=True)

    if result.returncode == 0:
        await emit(PipelineEvent(type="run_completed"))
        return "completed", None
    else:
        stderr_tail = result.stderr.decode(errors="replace")[-600:]
        error = f"nextflow exit {result.returncode}: {stderr_tail}"
        await emit(PipelineEvent(type="run_failed", payload={"error": error}))
        return "failed", error
