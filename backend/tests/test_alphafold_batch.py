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


# --- Dual-mode steps (NextflowEngine wiring) -------------------------------------
#
# submit_alphafold_batch attaches BOTH a Python handler AND a shell command to
# every WorkflowStep so the same tool runs through either engine. The handler is
# used by LocalWorkflowEngine; the command is used by NextflowEngine. These tests
# verify the dual-mode contract without going near a real `nextflow` binary —
# the engine-level subprocess machinery is exercised separately by
# test_nextflow_engine.py.


def test_each_step_carries_both_handler_and_command() -> None:
    """Every step submit_alphafold_batch creates must have BOTH handler (Local
    path) and command (Nextflow path) set; that's what makes the engine swap a
    config change rather than a refactor."""
    from bioforge.tools.structure.alphafold_batch import _make_step_command, _make_step_handler

    handler = _make_step_handler("P38398", include_pdb_text=False, max_pdb_kb=500)
    command = _make_step_command("alphafold_P38398", "P38398", include_pdb_text=False, max_pdb_kb=500)

    assert callable(handler), "handler must be an async callable for LocalWorkflowEngine"
    assert isinstance(command, str) and command, "command must be a non-empty shell string for NextflowEngine"


def test_step_command_invokes_cli_module_with_args() -> None:
    """The generated command shells out to `python -m bioforge.cli.fetch_alphafold`
    with the expected flags. Pinning the shape locks the bridge to the CLI module."""
    from bioforge.tools.structure.alphafold_batch import _make_step_command

    cmd = _make_step_command("alphafold_P38398", "P38398", include_pdb_text=False, max_pdb_kb=500)
    assert "bioforge.cli.fetch_alphafold" in cmd
    assert "--uniprot" in cmd
    assert "P38398" in cmd
    assert "--out" in cmd
    assert "alphafold_P38398.json" in cmd
    assert "--max-pdb-kb 500" in cmd
    # Default include_pdb_text=False → no flag.
    assert "--include-pdb-text" not in cmd


def test_step_command_propagates_include_pdb_flag() -> None:
    from bioforge.tools.structure.alphafold_batch import _make_step_command

    cmd = _make_step_command("alphafold_P38398", "P38398", include_pdb_text=True, max_pdb_kb=1000)
    assert "--include-pdb-text" in cmd
    assert "--max-pdb-kb 1000" in cmd


def test_step_command_uses_current_python_interpreter() -> None:
    """The command must use sys.executable so the Nextflow process reuses the
    parent's Python — that's where `bioforge` is importable."""
    import sys

    from bioforge.tools.structure.alphafold_batch import _make_step_command

    cmd = _make_step_command("alphafold_P38398", "P38398", include_pdb_text=False, max_pdb_kb=500)
    # sys.executable may contain backslashes (Windows) or spaces; shlex.quote handles both.
    # We don't pin the full path string but require it appears as a prefix of the command.
    expected_prefix = sys.executable
    # shlex.quote wraps Windows paths in single quotes when they contain backslashes.
    # Accept either the raw path or the quoted form.
    assert expected_prefix in cmd or expected_prefix.replace("\\", "\\\\") in cmd or f"'{expected_prefix}'" in cmd


def test_make_default_engine_local_when_env_unset(monkeypatch) -> None:
    """Default factory returns LocalWorkflowEngine when BIOFORGE_NEXTFLOW_ENABLED is unset."""
    from bioforge.tools.structure.alphafold_batch import _make_default_engine

    monkeypatch.delenv("BIOFORGE_NEXTFLOW_ENABLED", raising=False)
    engine = _make_default_engine()
    assert type(engine).__name__ == "LocalWorkflowEngine"


def test_make_default_engine_nextflow_when_env_set(monkeypatch) -> None:
    """Setting the env var to a truthy value flips the factory to NextflowEngine.
    The engine refuses to actually run anything without the binary, but
    construction works."""
    from bioforge.tools.structure.alphafold_batch import _make_default_engine

    monkeypatch.setenv("BIOFORGE_NEXTFLOW_ENABLED", "true")
    engine = _make_default_engine()
    assert type(engine).__name__ == "NextflowEngine"


def test_make_default_engine_treats_false_string_as_disabled(monkeypatch) -> None:
    """Env vars are strings; explicitly setting to 'false' / '0' must NOT enable Nextflow."""
    from bioforge.tools.structure.alphafold_batch import _make_default_engine

    monkeypatch.setenv("BIOFORGE_NEXTFLOW_ENABLED", "false")
    assert type(_make_default_engine()).__name__ == "LocalWorkflowEngine"
    monkeypatch.setenv("BIOFORGE_NEXTFLOW_ENABLED", "0")
    assert type(_make_default_engine()).__name__ == "LocalWorkflowEngine"


async def test_end_to_end_through_nextflow_engine(monkeypatch, tmp_path) -> None:
    """Run submit_alphafold_batch through a real NextflowEngine instance with an
    INJECTED subprocess runner — no `nextflow` binary required. The fake runner
    simulates Nextflow's behavior: writes the trace file + the per-step JSON
    output files, returns exit 0.

    This is the most important test in the file: it proves the dual-mode wiring
    is correct end-to-end (engine selection → command generation → trace parsing
    → output collection → ProteinResult aggregation)."""
    from bioforge.workflows.nextflow_engine import NextflowEngine, _SubprocessResult

    requested_ids = ["P38398", "P04637"]
    expected_step_names = [f"alphafold_{uid}" for uid in requested_ids]

    async def fake_runner(argv, work_dir) -> _SubprocessResult:
        from pathlib import Path

        wd = Path(work_dir)
        # Simulate the publishDir copy: write {step_name}.json for each step into work_dir.
        for step_name, uid in zip(expected_step_names, requested_ids, strict=True):
            output_payload = {
                "uniprot_id": uid,
                "entry_id": f"AF-{uid}-F1",
                "average_plddt": 75.0,
                "caveats": ["fixture from fake nextflow runner"],
            }
            (wd / f"{step_name}.json").write_text(__import__("json").dumps(output_payload), encoding="utf-8")
        # Simulate the trace file: one COMPLETED row per task.
        trace_lines = ["task_id\tname\tstatus\texit"]
        for i, step_name in enumerate(expected_step_names, start=1):
            trace_lines.append(f"{i}\t{step_name}\tCOMPLETED\t0")
        (wd / "trace.txt").write_text("\n".join(trace_lines) + "\n", encoding="utf-8")
        return _SubprocessResult(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setenv("BIOFORGE_NEXTFLOW_ENABLED", "true")
    engine = NextflowEngine(
        work_dir=tmp_path / "nf_work",
        subprocess_runner=fake_runner,
    )
    af_batch_module.set_engine(engine)

    try:
        out = await submit_alphafold_batch(AlphaFoldBatchInput(uniprot_ids=requested_ids))
    finally:
        # Restore the default engine for subsequent tests.
        af_batch_module.set_engine(LocalWorkflowEngine())

    assert isinstance(out, AlphaFoldBatchOutput)
    assert out.status == "completed"
    assert out.successes == 2
    assert out.failures == 0
    assert [r.uniprot_id for r in out.results] == requested_ids
    for r in out.results:
        assert r.success is True
        assert r.structure is not None
        assert r.structure["entry_id"] == f"AF-{r.uniprot_id}-F1"
    # The NextflowEngine caveat replaces the LocalWorkflowEngine one.
    assert any("NextflowEngine" in c for c in out.caveats)
    assert not any("LocalWorkflowEngine ran" in c for c in out.caveats)


async def test_nextflow_path_caveat_mentions_cli_module(monkeypatch, tmp_path) -> None:
    """The NextflowEngine caveat explicitly names the CLI module so users
    debugging a stuck Nextflow run know where to look."""
    from bioforge.workflows.nextflow_engine import NextflowEngine, _SubprocessResult

    async def fake_runner(argv, work_dir) -> _SubprocessResult:
        from pathlib import Path

        wd = Path(work_dir)
        (wd / "alphafold_P38398.json").write_text(
            __import__("json").dumps({"uniprot_id": "P38398", "entry_id": "AF-P38398-F1"}),
            encoding="utf-8",
        )
        (wd / "trace.txt").write_text(
            "task_id\tname\tstatus\texit\n1\talphafold_P38398\tCOMPLETED\t0\n", encoding="utf-8"
        )
        return _SubprocessResult(returncode=0)

    monkeypatch.setenv("BIOFORGE_NEXTFLOW_ENABLED", "true")
    engine = NextflowEngine(work_dir=tmp_path / "nf_work", subprocess_runner=fake_runner)
    af_batch_module.set_engine(engine)
    try:
        out = await submit_alphafold_batch(AlphaFoldBatchInput(uniprot_ids=["P38398"]))
    finally:
        af_batch_module.set_engine(LocalWorkflowEngine())

    assert any("bioforge.cli.fetch_alphafold" in c for c in out.caveats)


# --- Live Nextflow integration (opt-in) ------------------------------------------


@pytest.mark.nextflow
async def test_live_nextflow_run_brca1() -> None:
    """End-to-end through a REAL `nextflow` binary on PATH. Hits live EBI AlphaFold.

    Deselected by default — only runs when -m nextflow is explicitly passed.
    Skipped if the binary isn't available (e.g. on Windows-native; this test
    is meant for WSL2 / Linux dev loops + CI with nf-core/setup-nextflow).

    Smallest possible exercise: one well-known UniProt (P38398 = BRCA1).
    Asserts the result shape; biology assertions are covered by the
    fetch_alphafold_structure tool's own tests."""
    import os
    import shutil

    from bioforge.workflows.nextflow_engine import NextflowEngine

    if shutil.which("nextflow") is None:
        pytest.skip("`nextflow` not on PATH; install Nextflow or run from WSL2.")

    os.environ["BIOFORGE_NEXTFLOW_ENABLED"] = "true"
    engine = NextflowEngine()
    af_batch_module.set_engine(engine)
    try:
        out = await submit_alphafold_batch(AlphaFoldBatchInput(uniprot_ids=["P38398"]))
    finally:
        af_batch_module.set_engine(LocalWorkflowEngine())
        os.environ.pop("BIOFORGE_NEXTFLOW_ENABLED", None)

    assert out.status == "completed"
    assert out.successes == 1
    rec = out.results[0]
    assert rec.success is True
    assert rec.structure is not None
    assert rec.structure["uniprot_id"] == "P38398"
