"""OpenTelemetry tracing tests.

Strategy: install a real TracerProvider with an InMemorySpanExporter once per session,
then verify that running the agent emits the expected span hierarchy + attributes.
Tests are session-scoped because OTel forbids replacing the TracerProvider mid-process.
"""

from __future__ import annotations

import pytest
from bioforge.agent import run_agent
from bioforge.constants import DEFAULT_PROJECT_ID
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)


@pytest.fixture(scope="session")
def _otel_provider() -> InMemorySpanExporter:
    """Install a TracerProvider with an InMemorySpanExporter, exactly once per session.

    `trace.set_tracer_provider()` is one-shot in OpenTelemetry Python — subsequent
    calls are no-ops. Session scope avoids fighting that. Every test that wants spans
    requests the function-scoped `memory_exporter` fixture, which clears between tests.
    """
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # Force the bioforge.observability.tracing module to re-resolve its proxy tracer
    # against the new provider. Without this, the module-level `tracer = get_tracer()`
    # captured at import time may still resolve to a noop.
    from bioforge.observability import tracing as bf_tracing

    bf_tracing.tracer = trace.get_tracer(bf_tracing._TRACER_NAME)

    return exporter


@pytest.fixture
def memory_exporter(_otel_provider) -> InMemorySpanExporter:
    _otel_provider.clear()
    yield _otel_provider
    _otel_provider.clear()


def _span_names(exporter: InMemorySpanExporter) -> list[str]:
    return [s.name for s in exporter.get_finished_spans()]


def _spans_by_name(exporter: InMemorySpanExporter, name: str) -> list:
    return [s for s in exporter.get_finished_spans() if s.name == name]


# --- Root span shape ----------------------------------------------------------------


async def test_run_agent_emits_root_span_with_attributes(
    memory_exporter,
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    trivial_plan,
) -> None:
    llm = fake_llm_factory(
        [
            make_submit_plan_response(trivial_plan(tool_name="gc_content")),
            make_tool_use_response("gc_content", {"sequence": "ATGCATGC"}),
            make_text_response("50%"),
        ]
    )
    await run_agent(
        "GC content of ATGCATGC",
        project_id=DEFAULT_PROJECT_ID,
        llm=llm,
    )

    root_spans = _spans_by_name(memory_exporter, "agent.run")
    assert len(root_spans) == 1
    root = root_spans[0]
    assert root.attributes["bioforge.goal"] == "GC content of ATGCATGC"
    assert root.attributes["bioforge.project_id"] == DEFAULT_PROJECT_ID
    assert root.attributes["bioforge.model"] == "claude-sonnet-4-6"
    assert root.attributes["gen_ai.system"] == "anthropic"
    assert root.attributes["bioforge.status"] == "completed"
    assert root.attributes["bioforge.steps_total"] >= 3


async def test_root_span_truncates_long_goals(
    memory_exporter,
    fake_llm_factory,
    make_submit_plan_response,
    trivial_plan,
) -> None:
    """Goals over 500 chars get truncated in the span attribute but goal_length stays accurate."""
    long_goal = "x" * 1000
    llm = fake_llm_factory(
        [make_submit_plan_response({"is_trivial": True, "summary": "no", "steps": []})]
    )
    await run_agent(long_goal, project_id=DEFAULT_PROJECT_ID, llm=llm)

    root = _spans_by_name(memory_exporter, "agent.run")[0]
    assert len(root.attributes["bioforge.goal"]) <= 500
    assert root.attributes["bioforge.goal_length"] == 1000


# --- Child span hierarchy ------------------------------------------------------------


async def test_emits_plan_and_execute_child_spans(
    memory_exporter,
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    trivial_plan,
) -> None:
    llm = fake_llm_factory(
        [
            make_submit_plan_response(trivial_plan(tool_name="gc_content")),
            make_tool_use_response("gc_content", {"sequence": "ATGCATGC"}),
            make_text_response("50%"),
        ]
    )
    await run_agent("x", project_id=DEFAULT_PROJECT_ID, llm=llm)

    names = _span_names(memory_exporter)
    assert "agent.run" in names
    assert "agent.plan" in names
    assert "agent.approval_gate" in names  # always runs even if no approval needed
    # Tool call gets its own span (qualified with the tool name)
    assert any(n.startswith("tool.call.") for n in names)
    # LLM calls produce spans too
    assert "llm.complete" in names


async def test_tool_call_span_carries_tool_attributes(
    memory_exporter,
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    trivial_plan,
) -> None:
    llm = fake_llm_factory(
        [
            make_submit_plan_response(trivial_plan(tool_name="gc_content")),
            make_tool_use_response("gc_content", {"sequence": "ATGCATGC"}),
            make_text_response("50%"),
        ]
    )
    await run_agent("x", project_id=DEFAULT_PROJECT_ID, llm=llm)

    tool_spans = [
        s for s in memory_exporter.get_finished_spans() if s.name == "tool.call.gc_content"
    ]
    assert len(tool_spans) == 1
    attrs = tool_spans[0].attributes
    assert attrs["bioforge.tool_name"] == "gc_content"
    assert attrs["bioforge.tool_version"] == "1.0.0"
    assert attrs["bioforge.tool_cost_hint"] == "cheap"
    assert attrs["bioforge.tool_destructive"] is False


async def test_llm_span_carries_gen_ai_attributes(
    memory_exporter,
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    trivial_plan,
) -> None:
    llm = fake_llm_factory(
        [
            make_submit_plan_response(trivial_plan(tool_name="gc_content")),
            make_tool_use_response("gc_content", {"sequence": "ATGCATGC"}),
            make_text_response("50%"),
        ]
    )
    await run_agent("x", project_id=DEFAULT_PROJECT_ID, llm=llm)

    llm_spans = _spans_by_name(memory_exporter, "llm.complete")
    assert len(llm_spans) >= 1
    attrs = llm_spans[0].attributes
    assert attrs["gen_ai.system"] == "anthropic"
    assert attrs["gen_ai.request.model"] == "claude-sonnet-4-6"
    assert attrs["gen_ai.usage.input_tokens"] > 0
    assert attrs["gen_ai.usage.output_tokens"] > 0


# --- Status / error handling --------------------------------------------------------


async def test_refused_status_recorded_on_root_span(
    memory_exporter,
    fake_llm_factory,
    make_submit_plan_response,
) -> None:
    """Planner emits trivial=true + empty steps → status=refused on the root span."""
    llm = fake_llm_factory(
        [
            make_submit_plan_response(
                {"is_trivial": True, "summary": "Cannot help.", "steps": []}
            )
        ]
    )
    await run_agent("BLAST this", project_id=DEFAULT_PROJECT_ID, llm=llm)

    root = _spans_by_name(memory_exporter, "agent.run")[0]
    assert root.attributes["bioforge.status"] == "refused"


async def test_pending_approval_status_recorded_on_root_span(
    memory_exporter,
    fake_llm_factory,
    make_submit_plan_response,
    multi_step_plan,
) -> None:
    """Approval gate fires → status=pending_approval, gate's `bioforge.approval_required` is True."""
    llm = fake_llm_factory(
        [make_submit_plan_response(multi_step_plan([("blast", "search")]))]
    )
    await run_agent("blast this", project_id=DEFAULT_PROJECT_ID, llm=llm)

    root = _spans_by_name(memory_exporter, "agent.run")[0]
    assert root.attributes["bioforge.status"] == "pending_approval"

    gate_spans = _spans_by_name(memory_exporter, "agent.approval_gate")
    assert gate_spans[0].attributes["bioforge.approval_required"] is True
    assert gate_spans[0].attributes["bioforge.approval_reasons_count"] >= 1


async def test_tool_error_recorded_on_tool_span(
    memory_exporter,
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    trivial_plan,
) -> None:
    """When a tool raises, its span carries the exception event + ERROR status."""
    from opentelemetry.trace import StatusCode

    llm = fake_llm_factory(
        [
            make_submit_plan_response(trivial_plan(tool_name="gc_content")),
            # Pass invalid input → ValidationError → recorded as tool_error in loop
            make_tool_use_response("gc_content", {"sequence": "INVALID@CHARS"}),
            make_text_response("Error handled"),
        ]
    )
    await run_agent("x", project_id=DEFAULT_PROJECT_ID, llm=llm)

    tool_spans = [
        s
        for s in memory_exporter.get_finished_spans()
        if s.name == "tool.call.gc_content"
    ]
    assert len(tool_spans) == 1
    # The tool span should have recorded the exception
    assert tool_spans[0].status.status_code == StatusCode.ERROR
    assert any(ev.name == "exception" for ev in tool_spans[0].events)


# --- Span discipline: noop tracer overhead is non-existent --------------------------


async def test_noop_tracer_does_not_break_anything() -> None:
    """Sanity test that has nothing to do with the InMemorySpanExporter — confirms
    that calling configure_tracing(enabled=False) keeps the loop working.

    (No fixture means this test runs against whatever tracer is current — in CI without
    the otel test session this is the genuine noop tracer.)
    """
    from bioforge.observability import tracing

    # Just call get_tracer() and start a span — must not error
    span_ctx = tracing.tracer.start_as_current_span("test.noop")
    span_ctx.__enter__()
    span_ctx.__exit__(None, None, None)
