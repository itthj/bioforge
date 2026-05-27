"""Long-running workflow infrastructure — Phase 5 foundations.

A "workflow" here is a multi-step computation that lives outside the agent's
in-process tool-use loop because it:
  - Takes minutes to hours (BLAST against a large database, AlphaFold2
    prediction, RNA-seq pipeline).
  - Has well-defined inputs/outputs that benefit from a process manager
    (retries, resume, parallel fanout).
  - May run on different hardware than the agent (GPU, HPC cluster).

The agent submits a workflow, streams progress, and reads back results.
Implementations:
  - LocalWorkflowEngine: in-process, sequential. Phase 5 baseline so the
    interface is exercised end-to-end before any external dependency.
  - NextflowEngine: shells out to a Nextflow process. Stubbed in Phase 5.5.
  - Future: SLURMEngine for HPC, KubernetesEngine for cloud.

This package is contracts + a working local stub — no Nextflow / k8s
dependencies yet. See docs/phase5_architecture.md for the full plan.
"""

from bioforge.workflows.engine import (
    LocalWorkflowEngine,
    WorkflowEngine,
    WorkflowEvent,
    WorkflowRun,
    WorkflowStatus,
    WorkflowStep,
)

__all__ = [
    "LocalWorkflowEngine",
    "WorkflowEngine",
    "WorkflowEvent",
    "WorkflowRun",
    "WorkflowStatus",
    "WorkflowStep",
]
