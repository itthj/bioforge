"""Tests for submit_alphafold_batch — the Phase 5.4 workflow-using tool.

The tool routes through `bioforge.tools.registry.execute_tool(fetch_alphafold_structure)`
on each step. To keep the suite hermetic, we monkeypatch `_fetch_alphafold` in
the structure.fetch_alphafold module so the real network call never fires.

Engine: we use the real LocalWorkflowEngine — it's fast (in-process,
sequential) and lets us exercise the full submit → stream → get_run cycle.
"""

from __future__ import annotations

import pydantic
import pytest
from bioforge.tools.base import ToolError
from bioforge.tools.structure import alphafold_batch as af_batch_module
from bioforge.tools.structure import fetch_alphafold as af_module
from bioforge.tools.structure.alphafold_batch import (
    AlphaFoldBatchInput,
    AlphaFoldBatchOutput,
    submit_alphafold_batch,
)
from bioforge.workflows.engine import LocalWorkflowEngine

# --- Stub helpers ----------------------------------------------------------------


def _fake_alphafold_payload(uniprot_id: str) -> tuple[dict, str | None]:
    """Build a (metadata, pdb_text) tuple matching the real _fetch_alphafold contract."""
    return (
        {
            "entryId": f"AF-{uniprot_id}-F1",
            "organismScientificName": "Homo sapiens",
            "geneSymbol": f"GENE_{uniprot_id}",
            "uniprotDescription": f"Mock description for {uniprot_id}",
            "uniprotSequence": "MAA" * 50,  # 150 aa stub
            "pdbUrl": f"https://alphafold.ebi.ac.uk/files/AF-{uniprot_id}-F1-model_v4.pdb",
            "cifUrl": f"https://alphafold.ebi.ac.uk/files/AF-{uniprot_id}-F1-model_v4.cif",
            "paeImageUrl": None,
            "latestVersion": 4,
            "modelCreatedDate": "2023-01-01",
        },
        # Minimal PDB ATOM lines so the parser can extract pLDDT.
        "\n".join(
            f"ATOM  {i:>5}  CA  ALA A{i:>4d}      0.000   0.000   0.000  1.00 80.00          C" for i in range(1, 151)
        ),
    )


@pytest.fixture(autouse=True)
def patch_alphafold(monkeypatch):
    """Patch _fetch_alphafold to return a deterministic per-ID payload so all
    tests in this file run hermetically. Real network is never hit."""

    async def fake(uniprot_id: str) -> tuple[dict, str | None]:
        return _fake_alphafold_payload(uniprot_id)

    monkeypatch.setattr(af_module, "_fetch_alphafold", fake)


@pytest.fixture(autouse=True)
def reset_engine():
    """Each test gets a fresh LocalWorkflowEngine. Without this, run_ids
    accumulate across tests and any future state leak would be confusing."""
    af_batch_module.set_engine(LocalWorkflowEngine())
    yield
    af_batch_module.set_engine(LocalWorkflowEngine())


# --- Input validation ------------------------------------------------------------


def test_input_rejects_empty_list() -> None:
    with pytest.raises(pydantic.ValidationError):
        AlphaFoldBatchInput(uniprot_ids=[])


def test_input_rejects_oversized_batch() -> None:
    with pytest.raises(pydantic.ValidationError):
        AlphaFoldBatchInput(uniprot_ids=[f"P{i:05d}" for i in range(60)])


def test_input_rejects_malformed_ids() -> None:
    with pytest.raises(pydantic.ValidationError, match="UniProt"):
        AlphaFoldBatchInput(uniprot_ids=["not_a_uniprot"])


def test_input_dedupes_repeated_ids() -> None:
    inp = AlphaFoldBatchInput(uniprot_ids=["P38398", "P38398", "P04637"])
    assert inp.uniprot_ids == ["P38398", "P04637"]


def test_input_uppercases_lowercase_ids() -> None:
    inp = AlphaFoldBatchInput(uniprot_ids=["p38398", "p04637"])
    assert inp.uniprot_ids == ["P38398", "P04637"]


# --- End-to-end happy path -------------------------------------------------------


async def test_three_protein_batch_returns_one_result_per_id() -> None:
    out = await submit_alphafold_batch(AlphaFoldBatchInput(uniprot_ids=["P38398", "P04637", "P00533"]))
    assert isinstance(out, AlphaFoldBatchOutput)
    assert out.status == "completed"
    assert out.total_proteins == 3
    assert out.successes == 3
    assert out.failures == 0
    assert len(out.results) == 3
    assert [r.uniprot_id for r in out.results] == ["P38398", "P04637", "P00533"]
    for r in out.results:
        assert r.success is True
        assert r.structure is not None
        assert r.structure["uniprot_id"] == r.uniprot_id
        assert r.error is None


async def test_workflow_run_id_is_threaded_into_output() -> None:
    """The run_id pins this batch to a workflow-engine trace the user can reference later."""
    out = await submit_alphafold_batch(AlphaFoldBatchInput(uniprot_ids=["P38398"]))
    assert out.run_id
    # uuid4().hex shape — 32 lowercase hex chars.
    assert len(out.run_id) == 32
    assert all(c in "0123456789abcdef" for c in out.run_id)


async def test_dedup_collapses_input_order_after_uppercasing() -> None:
    out = await submit_alphafold_batch(AlphaFoldBatchInput(uniprot_ids=["p38398", "P38398", "p04637"]))
    assert out.total_proteins == 2
    assert [r.uniprot_id for r in out.results] == ["P38398", "P04637"]


# --- Failure propagation ---------------------------------------------------------


async def test_one_failing_protein_marks_run_failed(monkeypatch) -> None:
    """If one of the protein fetches raises, the run goes to status=failed.
    LocalWorkflowEngine stops on first failure (sequential), so later proteins
    never run and are surfaced as failures with the run-level error message."""

    async def fake(uniprot_id: str) -> tuple[dict, str | None]:
        if uniprot_id == "BADBAD":
            raise ToolError("No AlphaFold prediction available for 'BADBAD'.")
        return _fake_alphafold_payload(uniprot_id)

    monkeypatch.setattr(af_module, "_fetch_alphafold", fake)

    out = await submit_alphafold_batch(AlphaFoldBatchInput(uniprot_ids=["P38398", "BADBAD", "P04637"]))
    assert out.status == "failed"
    # The first ID succeeded; the failure stopped the second; third never ran.
    succeeded = [r for r in out.results if r.success]
    failed = [r for r in out.results if not r.success]
    assert [r.uniprot_id for r in succeeded] == ["P38398"]
    assert {r.uniprot_id for r in failed} == {"BADBAD", "P04637"}
    assert any("No AlphaFold prediction" in (r.error or "") for r in failed)


async def test_engine_submit_error_surfaces_as_tool_error() -> None:
    """If the engine itself refuses to accept the submission (e.g. cycle in
    deps, which we don't construct here, or future RPC failure), the tool
    must surface that as a ToolError rather than a stack trace."""

    class BrokenEngine:
        async def submit(self, steps):
            raise RuntimeError("engine offline")

        async def stream_progress(self, run_id):  # pragma: no cover — not reached
            yield

        async def cancel(self, run_id):
            return None

        async def get_run(self, run_id):
            raise KeyError(run_id)

    af_batch_module.set_engine(BrokenEngine())  # type: ignore[arg-type]
    with pytest.raises(ToolError, match="refused submission"):
        await submit_alphafold_batch(AlphaFoldBatchInput(uniprot_ids=["P38398"]))


# --- Caveat surface --------------------------------------------------------------


async def test_caveats_disclose_sequential_local_engine_behavior() -> None:
    out = await submit_alphafold_batch(AlphaFoldBatchInput(uniprot_ids=["P38398"]))
    blob = " ".join(out.caveats).lower()
    assert "computational" in blob  # the AlphaFold-is-prediction caveat
    assert "sequentially" in blob or "sequential" in blob  # engine behavior caveat


async def test_failed_run_emits_extra_caveat(monkeypatch) -> None:
    async def fake(uniprot_id: str) -> tuple[dict, str | None]:
        raise ToolError("network down")

    monkeypatch.setattr(af_module, "_fetch_alphafold", fake)
    out = await submit_alphafold_batch(AlphaFoldBatchInput(uniprot_ids=["P38398"]))
    assert out.status == "failed"
    assert any("Workflow failed" in c for c in out.caveats)


# --- Registry --------------------------------------------------------------------


async def test_tool_registered_as_expensive_with_structure_tags() -> None:
    from bioforge.tools.registry import get_tool

    spec = get_tool("submit_alphafold_batch")
    assert spec.cost_hint == "expensive"
    assert {"structure", "alphafold", "workflow"} <= set(spec.tags)
    assert any("Jumper" in c or "Varadi" in c for c in spec.citations)


# --- Engine injection contract ---------------------------------------------------


def test_set_engine_and_get_engine_roundtrip() -> None:
    """The injection seam tests + future code use to swap engines (Nextflow,
    SLURM, etc.)."""
    custom = LocalWorkflowEngine()
    af_batch_module.set_engine(custom)
    assert af_batch_module.get_engine() is custom
