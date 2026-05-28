"""Tests for the Phase 5 LocalWorkflowEngine + the WorkflowEngine Protocol.

The engine is small but has several state-machine paths worth nailing
down before anything builds on it: dep ordering, cycle detection,
cancellation mid-run, step failure propagation, and progress streaming.
"""

from __future__ import annotations

import asyncio

import pytest
from bioforge.workflows import (
    LocalWorkflowEngine,
    WorkflowEngine,
    WorkflowStatus,
    WorkflowStep,
)
from bioforge.workflows.engine import _topological_sort
from bioforge.workflows.nextflow_engine import NextflowEngine

# --- Topological sort ---------------------------------------------------------


def _step(name: str, depends_on: list[str] | None = None) -> WorkflowStep:
    async def _noop(_: dict) -> dict:
        return {}

    return WorkflowStep(name=name, handler=_noop, depends_on=depends_on or [])


def test_topo_sort_preserves_dependency_order() -> None:
    a = _step("a")
    b = _step("b", depends_on=["a"])
    c = _step("c", depends_on=["b"])
    # Submit them out of natural order — sort should still produce a, b, c.
    ordered = _topological_sort([c, a, b])
    assert [s.name for s in ordered] == ["a", "b", "c"]


def test_topo_sort_detects_cycle() -> None:
    a = _step("a", depends_on=["b"])
    b = _step("b", depends_on=["a"])
    with pytest.raises(ValueError, match="Cycle"):
        _topological_sort([a, b])


def test_topo_sort_rejects_unknown_dependency() -> None:
    a = _step("a", depends_on=["does_not_exist"])
    with pytest.raises(ValueError, match="Unknown dependency"):
        _topological_sort([a])


# --- LocalWorkflowEngine end-to-end -------------------------------------------


async def _collect(events_iter) -> list:
    out = []
    async for e in events_iter:
        out.append(e)
    return out


async def test_submit_completes_simple_workflow() -> None:
    engine = LocalWorkflowEngine()

    async def double(inputs: dict) -> dict:
        return {"value": inputs["x"] * 2}

    run = await engine.submit([WorkflowStep(name="double_it", handler=double, inputs={"x": 21})])
    events = await _collect(engine.stream_progress(run.run_id))
    final_run = await engine.get_run(run.run_id)

    types = [e.type for e in events]
    assert types == ["run_started", "step_started", "step_completed", "run_completed"]
    assert final_run.status == WorkflowStatus.completed
    assert final_run.step_outputs["double_it"] == {"value": 42}


async def test_steps_execute_in_dependency_order() -> None:
    engine = LocalWorkflowEngine()
    seen: list[str] = []

    async def record(name: str):
        async def _handler(_: dict) -> dict:
            seen.append(name)
            return {}

        return _handler

    a = WorkflowStep(name="a", handler=await record("a"))
    b = WorkflowStep(name="b", handler=await record("b"), depends_on=["a"])
    c = WorkflowStep(name="c", handler=await record("c"), depends_on=["b"])

    # Submit out of order — the engine must reorder.
    run = await engine.submit([c, a, b])
    await _collect(engine.stream_progress(run.run_id))
    assert seen == ["a", "b", "c"]


async def test_step_failure_propagates_to_run_failed() -> None:
    engine = LocalWorkflowEngine()

    async def boom(_: dict) -> dict:
        raise RuntimeError("kaboom")

    run = await engine.submit([WorkflowStep(name="boom", handler=boom)])
    events = await _collect(engine.stream_progress(run.run_id))

    types = [e.type for e in events]
    assert types == ["run_started", "step_started", "step_failed", "run_failed"]
    final_run = await engine.get_run(run.run_id)
    assert final_run.status == WorkflowStatus.failed
    assert "kaboom" in (final_run.error_message or "")


async def test_step_failure_aborts_subsequent_steps() -> None:
    """If 'a' fails, 'b' (which depends on it) should never run."""
    engine = LocalWorkflowEngine()
    b_ran = False

    async def fail(_: dict) -> dict:
        raise RuntimeError("nope")

    async def b_handler(_: dict) -> dict:
        nonlocal b_ran
        b_ran = True
        return {}

    a = WorkflowStep(name="a", handler=fail)
    b = WorkflowStep(name="b", handler=b_handler, depends_on=["a"])
    run = await engine.submit([a, b])
    await _collect(engine.stream_progress(run.run_id))
    assert b_ran is False


async def test_cancel_mid_run_stops_remaining_steps() -> None:
    engine = LocalWorkflowEngine()
    step_b_ran = False

    async def slow_a(_: dict) -> dict:
        await asyncio.sleep(0.05)
        return {}

    async def b_handler(_: dict) -> dict:
        nonlocal step_b_ran
        step_b_ran = True
        return {}

    a = WorkflowStep(name="a", handler=slow_a)
    b = WorkflowStep(name="b", handler=b_handler, depends_on=["a"])
    run = await engine.submit([a, b])
    # Cancel before slow_a completes. The cancel is registered between steps,
    # so step 'a' will finish and step 'b' will be skipped — that's the
    # cancellation point the engine guarantees.
    await asyncio.sleep(0.01)
    await engine.cancel(run.run_id)
    await _collect(engine.stream_progress(run.run_id))
    final_run = await engine.get_run(run.run_id)
    assert final_run.status == WorkflowStatus.cancelled
    assert step_b_ran is False


async def test_outputs_are_accessible_after_completion() -> None:
    engine = LocalWorkflowEngine()

    async def step_a(_: dict) -> dict:
        return {"a_output": "alpha"}

    async def step_b(_: dict) -> dict:
        return {"b_output": "beta"}

    a = WorkflowStep(name="a", handler=step_a)
    b = WorkflowStep(name="b", handler=step_b, depends_on=["a"])
    run = await engine.submit([a, b])
    await _collect(engine.stream_progress(run.run_id))
    final_run = await engine.get_run(run.run_id)
    assert final_run.step_outputs == {"a": {"a_output": "alpha"}, "b": {"b_output": "beta"}}


async def test_unknown_run_id_raises() -> None:
    engine = LocalWorkflowEngine()
    with pytest.raises(KeyError):
        await engine.get_run("not-a-real-id")
    with pytest.raises(KeyError):
        await engine.cancel("not-a-real-id")


# --- Protocol conformance -----------------------------------------------------


def test_local_engine_satisfies_protocol() -> None:
    engine = LocalWorkflowEngine()
    # runtime_checkable Protocol — pure isinstance check.
    assert isinstance(engine, WorkflowEngine)


def test_nextflow_engine_satisfies_protocol() -> None:
    """NextflowEngine still satisfies the WorkflowEngine Protocol after the
    Phase 5.5 implementation. Detailed behavior tests live in
    test_nextflow_engine.py — this is the minimal Protocol-conformance probe."""
    engine = NextflowEngine()
    assert isinstance(engine, WorkflowEngine)


async def test_nextflow_engine_refuses_without_feature_flag(monkeypatch) -> None:
    """Feature-flagged behind BIOFORGE_NEXTFLOW_ENABLED so an accidental swap
    from LocalWorkflowEngine surfaces as a clear error rather than blowing up
    on a missing `nextflow` binary."""
    monkeypatch.delenv("BIOFORGE_NEXTFLOW_ENABLED", raising=False)
    engine = NextflowEngine()
    with pytest.raises(RuntimeError, match="BIOFORGE_NEXTFLOW_ENABLED"):
        await engine.submit([_step("a")])
