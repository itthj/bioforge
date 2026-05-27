"""Nextflow workflow engine adapter — Phase 5.5 placeholder.

Submits workflows to a Nextflow process (https://www.nextflow.io/) which
provides retry, resume, fanout, container isolation, and HPC/cloud
scheduling out of the box.

NOT IMPLEMENTED YET. This stub:
  - Documents the Nextflow integration plan.
  - Raises NotImplementedError on every method so an accidental swap from
    LocalWorkflowEngine to NextflowEngine surfaces immediately.
  - Satisfies the WorkflowEngine Protocol for type-checking, so downstream
    code can already type its dependency as `WorkflowEngine` rather than
    `LocalWorkflowEngine`.

Implementation plan (Phase 5.5):
  1. Generate a .nf script from the WorkflowStep list — each step becomes
     a Nextflow process. Inputs/outputs map onto Nextflow channels.
  2. Shell out to `nextflow run` with `-with-report` + `-with-trace` so
     we can parse progress from the trace file.
  3. Stream events by tailing the .nextflow.log + trace file.
  4. Cancel by sending SIGINT to the Nextflow process.
  5. Map Nextflow's caching/resume onto our `get_run` so re-submitting an
     identical workflow returns the previous result without rerunning.

Until that lands, all real workflow execution goes through LocalWorkflowEngine.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from bioforge.workflows.engine import WorkflowEvent, WorkflowRun, WorkflowStep


class NextflowEngine:
    """Stub. Methods raise NotImplementedError. Implements WorkflowEngine
    Protocol so call sites can type their dependency as the abstract Protocol
    today and swap the implementation in Phase 5.5."""

    def __init__(self, nextflow_bin: str = "nextflow", work_dir: str = "./.nextflow_work") -> None:
        self.nextflow_bin = nextflow_bin
        self.work_dir = work_dir

    async def submit(self, steps: list[WorkflowStep]) -> WorkflowRun:
        raise NotImplementedError(
            "NextflowEngine is not implemented yet. Use LocalWorkflowEngine "
            "for now. See workflows/nextflow_engine.py for the implementation plan."
        )

    async def stream_progress(self, run_id: str) -> AsyncIterator[WorkflowEvent]:
        raise NotImplementedError("NextflowEngine.stream_progress is not implemented yet.")
        # Unreachable, but tells the type checker this is an async generator.
        yield  # type: ignore[unreachable]

    async def cancel(self, run_id: str) -> None:
        raise NotImplementedError("NextflowEngine.cancel is not implemented yet.")

    async def get_run(self, run_id: str) -> WorkflowRun:
        raise NotImplementedError("NextflowEngine.get_run is not implemented yet.")
