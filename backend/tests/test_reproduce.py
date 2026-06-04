"""Unit tests for the reproduce-in-code script renderer (P1)."""

from __future__ import annotations

from bioforge.agent.loop import AgentResult, AgentStep
from bioforge.provenance import render_reproduce_script


def _result(steps: list[AgentStep]) -> AgentResult:
    return AgentResult(
        goal="Compute GC content then BLAST the sequence",
        project_id="default-project",
        response_text="done",
        status="completed",
        model="claude-sonnet-4-test",
        steps=steps,
    )


def test_script_emits_tool_calls_in_order() -> None:
    steps = [
        AgentStep(idx=0, type="plan", duration_ms=1),
        AgentStep(
            idx=1,
            type="tool_call",
            duration_ms=5,
            tool_name="gc_content",
            tool_input={"sequence": "ATGC"},
            tool_output={"gc_percent": 50, "tool_version": "1.0.0"},
        ),
        AgentStep(
            idx=2,
            type="tool_call",
            duration_ms=9,
            tool_name="blast",
            tool_input={"sequence": "ATGCATGC", "database": "nt"},
            tool_output={"tool_version": "2.0.0"},
        ),
        AgentStep(idx=3, type="final", duration_ms=0),
    ]
    script = render_reproduce_script(_result(steps))
    assert "import asyncio" in script
    assert "from bioforge.tools.registry import execute_tool" in script
    assert "execute_tool('gc_content'" in script
    assert "execute_tool('blast'" in script
    assert "'sequence': 'ATGC'" in script
    assert "asyncio.run(main())" in script
    # Tool versions surface as comments.
    assert "v1.0.0" in script and "v2.0.0" in script
    # Order is preserved (gc_content ran before blast).
    assert script.index("gc_content") < script.index("blast")


def test_script_handles_a_no_tool_run() -> None:
    script = render_reproduce_script(_result([AgentStep(idx=0, type="final", duration_ms=0)]))
    assert "no tools" in script.lower()
    assert "asyncio.run(main())" in script


def test_script_is_deterministic() -> None:
    steps = [
        AgentStep(idx=1, type="tool_call", duration_ms=5, tool_name="gc_content", tool_input={"sequence": "ATGC"}),
    ]
    assert render_reproduce_script(_result(steps)) == render_reproduce_script(_result(steps))
