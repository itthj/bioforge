"""Phase 5.4: the first workflow-using tool.

Given a list of UniProt IDs, submit one `fetch_alphafold_structure` per ID
through a `WorkflowEngine`, stream progress events, wait for completion,
and return aggregated per-protein results.

Why route AlphaFold batching through the workflow engine instead of just
calling `fetch_alphafold_structure` N times in a `gather`:

  1. **Migration path.** The engine boundary is the seam where Nextflow
     (Phase 5.5) plugs in. With AlphaFold-Multimer or large proteomes,
     running ~hundreds of predictions on a GPU node is the real use case.
     A workflow-using tool today is a Nextflow-ready tool tomorrow.
  2. **Provenance.** Every step gets its own start/finish timestamps,
     a per-step error trail, and a run_id the user can reference later.
     A bare `gather` loses that structure.
  3. **Cancellation.** The engine exposes `cancel(run_id)` — a user who
     started a 50-protein batch and changed their mind has a clean abort
     path. Bare `gather` would need bespoke cancellation plumbing.

LocalWorkflowEngine ships in-process: steps run sequentially (no real
parallelism yet — that's a Nextflow concern). For a 10-protein batch
the total wall-clock is roughly 10 × per-call AlphaFold latency.

# Approval

`cost_hint="expensive"`. The batch performs N HTTP calls to EBI AlphaFold;
even though each is cheap, a 50-protein run is real wall-clock and real
network egress. Triggers the agent loop's approval gate.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import execute_tool, register_tool
from bioforge.workflows.engine import (
    LocalWorkflowEngine,
    WorkflowEngine,
    WorkflowStatus,
    WorkflowStep,
)

# Module-level default engine. Tests substitute via `set_engine()`. Production
# uses the LocalWorkflowEngine baseline (single process); a Nextflow-backed
# engine takes over once Phase 5.5 lands behind its feature flag.
_default_engine: WorkflowEngine = LocalWorkflowEngine()


def set_engine(engine: WorkflowEngine) -> None:
    """Override the module-level engine. Tests use this for injection."""
    global _default_engine
    _default_engine = engine


def get_engine() -> WorkflowEngine:
    return _default_engine


_UNIPROT_RE = re.compile(r"^[A-Z0-9]+$")


# --- Input / output schema -----------------------------------------------------------


class AlphaFoldBatchInput(ToolInput):
    uniprot_ids: list[str] = Field(
        ...,
        min_length=1,
        max_length=50,
        description=(
            "UniProt accessions to fetch AlphaFold predictions for. Examples: "
            "['P38398', 'P04637', 'P00533'] for BRCA1, TP53, EGFR. The batch is "
            "capped at 50 to keep wall-clock and EBI-traffic reasonable; for "
            "whole-proteome scans use a Nextflow pipeline against a local "
            "mirror instead."
        ),
    )
    include_pdb_text: bool = Field(
        default=False,
        description=(
            "Pass through to fetch_alphafold_structure. Default False here "
            "(unlike the single-call tool) because batched PDB text blows up "
            "the agent's context window — leave False unless you specifically "
            "want every structure rendered in the trace."
        ),
    )
    max_pdb_kb: int = Field(
        default=500,
        ge=10,
        le=5000,
        description="Per-protein PDB cap; only relevant when include_pdb_text=True.",
    )

    @field_validator("uniprot_ids")
    @classmethod
    def _validate_ids(cls, v: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in v:
            up = raw.strip().upper()
            if not (6 <= len(up) <= 10):
                raise ValueError(f"UniProt ID must be 6-10 chars; got {raw!r}")
            if not _UNIPROT_RE.match(up):
                raise ValueError(f"UniProt ID must be uppercase letters and digits; got {raw!r}")
            if up in seen:
                # Deduplicate transparently — sending the same ID twice would just produce
                # identical results and waste a workflow step.
                continue
            seen.add(up)
            cleaned.append(up)
        return cleaned


class ProteinResult(BaseModel):
    """Per-protein outcome. Either `structure` is populated (success) or
    `error` is (failure); never both."""

    uniprot_id: str
    success: bool
    structure: dict[str, Any] | None = Field(
        default=None,
        description="The full FetchAlphaFoldOutput as a dict, when the step succeeded.",
    )
    error: str | None = Field(default=None, description="Error message when the step failed.")
    duration_ms: int | None = Field(default=None, description="Per-step wall-clock if available.")


class AlphaFoldBatchOutput(ToolOutput):
    run_id: str = Field(description="Workflow run ID for this batch — for trace correlation.")
    status: str = Field(description="Terminal workflow status: completed / failed / cancelled.")
    total_proteins: int
    successes: int
    failures: int
    results: list[ProteinResult] = Field(
        description="One row per requested UniProt ID, in the input order (post-dedup).",
    )
    caveats: list[str] = Field(default_factory=list)


# --- Step handlers ------------------------------------------------------------------


def _make_step_handler(uniprot_id: str, include_pdb_text: bool, max_pdb_kb: int):
    """Build a step handler that calls fetch_alphafold_structure for one ID.

    Closing over the ID + flags rather than reading from `inputs` keeps each
    step self-describing in the trace. The engine still passes `inputs` to
    the handler; we just don't read from it here.
    """

    async def handler(_inputs: dict[str, Any]) -> dict[str, Any]:
        result = await execute_tool(
            "fetch_alphafold_structure",
            {
                "uniprot_id": uniprot_id,
                "include_pdb_text": include_pdb_text,
                "max_pdb_kb": max_pdb_kb,
            },
        )
        return result.model_dump()

    return handler


# --- Tool ----------------------------------------------------------------------------


@register_tool(
    name="submit_alphafold_batch",
    description=(
        "Fetch AlphaFold predicted structures for up to 50 UniProt accessions "
        "via the workflow engine. Submits one fetch_alphafold_structure step per "
        "ID, streams progress, returns aggregated per-protein results (success "
        "or per-step error). Use whenever the agent needs structures for a "
        "panel of proteins (kinome scan, GWAS-prioritized gene set, pathway "
        "members) rather than one at a time. cost_hint=expensive because the "
        "batch performs N network calls; the loop's approval gate confirms "
        "with the user before kicking off long runs."
    ),
    input_model=AlphaFoldBatchInput,
    output_model=AlphaFoldBatchOutput,
    version="1.0.0",
    citations=[
        "Jumper J et al. (2021) Highly accurate protein structure prediction with AlphaFold. Nature 596:583-589",
        "Varadi M et al. (2022) AlphaFold Protein Structure Database. Nucleic Acids Res 50:D439-D444 (EBI source)",
    ],
    cost_hint="expensive",
    destructive=False,
    tags=["structure", "alphafold", "workflow"],
)
async def submit_alphafold_batch(inp: AlphaFoldBatchInput) -> AlphaFoldBatchOutput:
    engine = get_engine()

    steps: list[WorkflowStep] = []
    for uid in inp.uniprot_ids:
        steps.append(
            WorkflowStep(
                name=f"alphafold_{uid}",
                handler=_make_step_handler(uid, inp.include_pdb_text, inp.max_pdb_kb),
                inputs={"uniprot_id": uid},
            )
        )

    try:
        run = await engine.submit(steps)
    except Exception as e:
        raise ToolError(f"Workflow engine refused submission: {type(e).__name__}: {e}") from e

    # Drain progress events until the run terminates. Events are emitted by
    # the engine for run_started / step_started / step_completed / step_failed /
    # run_completed / run_failed / run_cancelled. We just need to wait for the
    # stream to close (a None sentinel inside the engine).
    try:
        async for _event in engine.stream_progress(run.run_id):
            # The agent's outer SSE layer is what surfaces per-step progress to
            # the user; this inner loop only needs to advance the run to completion.
            pass
    except Exception as e:
        raise ToolError(f"Workflow stream failed for run {run.run_id}: {type(e).__name__}: {e}") from e

    final = await engine.get_run(run.run_id)

    results: list[ProteinResult] = []
    successes = 0
    failures = 0
    for uid in inp.uniprot_ids:
        step_name = f"alphafold_{uid}"
        output = final.step_outputs.get(step_name)
        if output is not None:
            results.append(ProteinResult(uniprot_id=uid, success=True, structure=output))
            successes += 1
        else:
            # The step either never ran (early failure of an earlier step) or
            # failed itself. We don't have per-step duration in step_outputs;
            # the run-level error message is what we have.
            err = final.error_message or "Step did not complete (run failed or was cancelled before this step)."
            results.append(ProteinResult(uniprot_id=uid, success=False, error=err))
            failures += 1

    caveats: list[str] = [
        "AlphaFold predictions are computational, not experimental. Per-residue pLDDT scores indicate per-region confidence — see each ProteinResult.structure for the full caveat list.",
        "LocalWorkflowEngine runs steps sequentially in-process. A 50-protein batch takes ~50× the single-call latency. For larger panels, use the (Phase 5.5) Nextflow engine which parallelizes across nodes.",
    ]
    if final.status == WorkflowStatus.failed:
        caveats.append(
            "Workflow failed mid-run — protein results after the failing step are marked as failures. "
            "Inspect run_id in the workflow engine for the exact failure point."
        )
    if final.status == WorkflowStatus.cancelled:
        caveats.append("Workflow was cancelled before completion; unfinished proteins are marked as failures.")

    return AlphaFoldBatchOutput(
        run_id=run.run_id,
        status=final.status.value,
        total_proteins=len(inp.uniprot_ids),
        successes=successes,
        failures=failures,
        results=results,
        caveats=caveats,
    )
