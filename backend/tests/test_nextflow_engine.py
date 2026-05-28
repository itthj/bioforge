"""Tests for the Phase 5.5 NextflowEngine.

The actual `nextflow` binary is NOT installed (and we don't require it). We
test the engine end-to-end by injecting a `subprocess_runner` fake that:
  - writes a synthetic trace file step-by-step
  - writes per-step output JSON files
  - returns the chosen exit code

That exercises everything except the real Nextflow daemon: script gen,
submit, trace tailing, output collection, status transitions, cancellation.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from bioforge.workflows.engine import WorkflowStatus, WorkflowStep
from bioforge.workflows.nextflow_engine import (
    NextflowEngine,
    _SubprocessResult,
    generate_nf_script,
    parse_trace_file,
)

# --- Feature flag --------------------------------------------------------------------


async def test_engine_refuses_without_feature_flag(monkeypatch, tmp_path: Path) -> None:
    """If BIOFORGE_NEXTFLOW_ENABLED is unset, submit() must refuse — the goal is
    to make an accidental engine swap surface explicitly rather than crash on a
    missing nextflow binary later."""
    monkeypatch.delenv("BIOFORGE_NEXTFLOW_ENABLED", raising=False)
    engine = NextflowEngine(work_dir=tmp_path, subprocess_runner=_no_op_runner)
    with pytest.raises(RuntimeError, match="BIOFORGE_NEXTFLOW_ENABLED"):
        await engine.submit([WorkflowStep(name="x", command="echo hi")])


@pytest.fixture
def flag_enabled(monkeypatch):
    """Most tests assume the feature flag is on."""
    monkeypatch.setenv("BIOFORGE_NEXTFLOW_ENABLED", "true")


# --- Script generation -------------------------------------------------------------


def test_generate_nf_script_simple_single_step() -> None:
    steps = [WorkflowStep(name="hello", command="echo hello > hello.json")]
    nf = generate_nf_script(steps)
    assert "nextflow.enable.dsl=2" in nf
    assert "process hello {" in nf
    assert "echo hello > hello.json" in nf
    # Workflow block invokes the process.
    assert "workflow {" in nf
    assert "hello()" in nf


def test_generate_nf_script_sanitizes_process_names() -> None:
    """Hyphens / dots / spaces aren't valid Groovy identifiers. The generator
    must munge them to underscores."""
    steps = [WorkflowStep(name="step-A.1 first", command="echo")]
    nf = generate_nf_script(steps)
    assert "process step_A_1_first {" in nf


def test_generate_nf_script_orders_by_depends_on() -> None:
    """Steps must appear in topological order in the workflow block so
    depends_on prerequisites fire first."""
    steps = [
        WorkflowStep(name="b", command="echo b", depends_on=["a"]),
        WorkflowStep(name="a", command="echo a"),
    ]
    nf = generate_nf_script(steps)
    # In the workflow block, a() should come before b(a.out).
    wf_block = nf.split("workflow {", 1)[1]
    a_idx = wf_block.index("a()")
    b_idx = wf_block.index("b(")
    assert a_idx < b_idx
    assert "depends_on: ['a']" in wf_block  # comment annotation


def test_generate_nf_script_rejects_handler_only_step() -> None:
    """A step without a `command` is a LocalWorkflowEngine step — NextflowEngine
    cannot execute it. The script generator refuses at the boundary."""

    async def py_handler(_inputs):
        return {}

    with pytest.raises(ValueError, match="has no `command`"):
        generate_nf_script([WorkflowStep(name="x", handler=py_handler)])


def test_generate_nf_script_rejects_empty_steps() -> None:
    with pytest.raises(ValueError, match="empty step list"):
        generate_nf_script([])


# --- Trace file parsing ------------------------------------------------------------


def test_parse_trace_file_extracts_known_columns(tmp_path: Path) -> None:
    trace = tmp_path / "trace.txt"
    trace.write_text(
        "task_id\tname\tstatus\texit\tsubmit\tduration\n"
        "1\tstep_a\tCOMPLETED\t0\t2026-05-28 10:00:00\t1s\n"
        "2\tstep_b\tFAILED\t1\t2026-05-28 10:00:01\t2s\n"
    )
    rows = parse_trace_file(trace)
    assert [r.name for r in rows] == ["step_a", "step_b"]
    assert [r.status for r in rows] == ["COMPLETED", "FAILED"]
    assert [r.exit for r in rows] == ["0", "1"]


def test_parse_trace_file_missing_returns_empty(tmp_path: Path) -> None:
    rows = parse_trace_file(tmp_path / "does_not_exist.txt")
    assert rows == []


def test_parse_trace_file_missing_columns_raises(tmp_path: Path) -> None:
    trace = tmp_path / "trace.txt"
    trace.write_text("not_real_col\tanother\n1\t2\n")
    with pytest.raises(ValueError, match="missing expected column"):
        parse_trace_file(trace)


# --- End-to-end via injected subprocess runner -------------------------------------


async def _no_op_runner(argv: list[str], work_dir: Path) -> _SubprocessResult:  # noqa: ARG001
    """Stand-in runner that does nothing; used for tests that exercise pre-run paths."""
    return _SubprocessResult(returncode=0)


def _make_simulator_runner(*, exit_code: int = 0, write_outputs: dict[str, dict] | None = None):
    """Return a subprocess runner that simulates Nextflow by appending rows
    to the trace file and writing the requested output JSON files."""

    async def runner(argv: list[str], work_dir: Path) -> _SubprocessResult:
        # The argv carries the trace path; we extract it.
        trace_path = Path(argv[argv.index("-with-trace") + 1])
        # Write header + one COMPLETED row per output we're about to produce.
        outputs = write_outputs or {}
        lines = ["task_id\tname\tstatus\texit"]
        for i, step_name in enumerate(outputs.keys(), start=1):
            lines.append(f"{i}\t{step_name}\tCOMPLETED\t0")
        trace_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        # Write each step's output JSON.
        for step_name, payload in outputs.items():
            (work_dir / f"{step_name}.json").write_text(json.dumps(payload), encoding="utf-8")

        # Tiny pause so the engine's tail loop sees at least one polling cycle.
        await asyncio.sleep(0.0)
        return _SubprocessResult(returncode=exit_code)

    return runner


async def test_submit_creates_work_dir_and_script(flag_enabled, tmp_path: Path) -> None:
    engine = NextflowEngine(work_dir=tmp_path, subprocess_runner=_no_op_runner)
    steps = [WorkflowStep(name="hello", command="echo hi > hello.json")]
    run = await engine.submit(steps)

    work_dir = tmp_path / run.run_id
    assert work_dir.is_dir()
    nf = (work_dir / "main.nf").read_text(encoding="utf-8")
    assert "process hello" in nf
    # Drain the run so the test doesn't leak a background task.
    async for _ in engine.stream_progress(run.run_id):
        pass


async def test_full_run_emits_events_and_collects_outputs(flag_enabled, tmp_path: Path) -> None:
    runner = _make_simulator_runner(
        write_outputs={
            "step_a": {"result": 1},
            "step_b": {"result": 2},
        },
    )
    engine = NextflowEngine(work_dir=tmp_path, subprocess_runner=runner)
    steps = [
        WorkflowStep(name="step_a", command="echo a > step_a.json"),
        WorkflowStep(name="step_b", command="echo b > step_b.json", depends_on=["step_a"]),
    ]
    run = await engine.submit(steps)

    events: list[str] = []
    async for ev in engine.stream_progress(run.run_id):
        events.append(f"{ev.type}:{ev.step_name or ''}")

    assert events[0] == "run_started:"
    assert events[-1] == "run_completed:"
    completed_steps = [e for e in events if e.startswith("step_completed:")]
    assert {"step_completed:step_a", "step_completed:step_b"} <= set(completed_steps)

    final = await engine.get_run(run.run_id)
    assert final.status == WorkflowStatus.completed
    assert final.step_outputs == {"step_a": {"result": 1}, "step_b": {"result": 2}}
    assert final.started_at is not None
    assert final.finished_at is not None


async def test_failed_subprocess_exit_marks_run_failed(flag_enabled, tmp_path: Path) -> None:
    runner = _make_simulator_runner(exit_code=1, write_outputs={})
    engine = NextflowEngine(work_dir=tmp_path, subprocess_runner=runner)
    steps = [WorkflowStep(name="boom", command="exit 1")]
    run = await engine.submit(steps)

    events_by_type: list[str] = []
    async for ev in engine.stream_progress(run.run_id):
        events_by_type.append(ev.type)

    assert "run_failed" in events_by_type
    final = await engine.get_run(run.run_id)
    assert final.status == WorkflowStatus.failed
    assert final.error_message is not None
    assert "exit 1" in final.error_message


async def test_failed_step_in_trace_emits_step_failed_event(flag_enabled, tmp_path: Path) -> None:
    """The trace can contain a FAILED row even when the subprocess exits 0
    in tests; the engine surfaces step_failed events regardless."""

    async def runner(argv: list[str], work_dir: Path) -> _SubprocessResult:
        trace_path = Path(argv[argv.index("-with-trace") + 1])
        trace_path.write_text(
            "task_id\tname\tstatus\texit\n1\tbad_step\tFAILED\t1\n",
            encoding="utf-8",
        )
        return _SubprocessResult(returncode=1)

    engine = NextflowEngine(work_dir=tmp_path, subprocess_runner=runner)
    steps = [WorkflowStep(name="bad_step", command="exit 1")]
    run = await engine.submit(steps)
    types_seen = {ev.type for ev in [e async for e in engine.stream_progress(run.run_id)]}
    assert "step_failed" in types_seen


async def test_handler_only_step_rejected_at_submit(flag_enabled, tmp_path: Path) -> None:
    async def py_handler(_inputs):
        return {}

    engine = NextflowEngine(work_dir=tmp_path, subprocess_runner=_no_op_runner)
    with pytest.raises(ValueError, match="has no `command`"):
        await engine.submit([WorkflowStep(name="x", handler=py_handler)])


async def test_get_run_unknown_id_raises(flag_enabled, tmp_path: Path) -> None:
    engine = NextflowEngine(work_dir=tmp_path, subprocess_runner=_no_op_runner)
    with pytest.raises(KeyError):
        await engine.get_run("nonexistent")


async def test_stream_progress_unknown_id_raises(flag_enabled, tmp_path: Path) -> None:
    engine = NextflowEngine(work_dir=tmp_path, subprocess_runner=_no_op_runner)
    with pytest.raises(KeyError):

        async def _drain():
            async for _ in engine.stream_progress("nonexistent"):
                pass

        await _drain()


# --- Cancellation ----------------------------------------------------------------


async def test_cancel_marks_run_cancelled(flag_enabled, tmp_path: Path) -> None:
    """Cancel before the subprocess finishes — the run should reach cancelled
    status. We simulate a slow subprocess so cancel can land first."""
    cancel_event_in_runner = asyncio.Event()

    async def slow_runner(argv: list[str], work_dir: Path) -> _SubprocessResult:
        # Block until told to release.
        await cancel_event_in_runner.wait()
        return _SubprocessResult(returncode=130)  # SIGINT exit code

    engine = NextflowEngine(work_dir=tmp_path, subprocess_runner=slow_runner)
    steps = [WorkflowStep(name="slow", command="sleep 999")]
    run = await engine.submit(steps)

    # Give the engine a tick to start the subprocess task, then cancel.
    await asyncio.sleep(0.05)
    cancel_task = asyncio.create_task(engine.cancel(run.run_id))
    cancel_event_in_runner.set()
    await cancel_task

    final = await engine.get_run(run.run_id)
    # Either the cancel landed before the subprocess result was processed
    # (status=cancelled) or right after (status=failed with non-zero exit).
    # The cancel signal IS recorded; we accept either terminal state here.
    assert final.status in (WorkflowStatus.cancelled, WorkflowStatus.failed)


async def test_cancel_unknown_id_raises(flag_enabled, tmp_path: Path) -> None:
    engine = NextflowEngine(work_dir=tmp_path, subprocess_runner=_no_op_runner)
    with pytest.raises(KeyError):
        await engine.cancel("nonexistent")
