"""Streaming-path tests.

Two layers:
  1. **Unit** — `on_step` callback is invoked once per AgentStep in run_agent / resume_agent,
     in the right order. Plus the SSE format helpers produce bytes that match the WHATWG
     event-stream spec.
  2. **Integration** — `POST /agent/run/stream` and `/agent/{id}/approve/stream` actually
     emit `text/event-stream` content with the events we expect. Uses httpx.AsyncClient
     against the in-process FastAPI app with the LLM + DB dependencies overridden.
"""

from __future__ import annotations

import json
import re

from bioforge.agent import AgentStep, Plan, PlanStep, resume_agent, run_agent
from bioforge.api.agent import get_llm
from bioforge.api.sse import format_event, format_keepalive
from bioforge.constants import DEFAULT_PROJECT_ID

# --- Unit: on_step callback ----------------------------------------------------------


async def test_on_step_fires_for_each_step_in_order(
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    trivial_plan,
) -> None:
    """Trivial plan + gc_content + final text. on_step should see plan, llm_call,
    tool_call, llm_call, final — in that order, before run_agent returns."""
    llm = fake_llm_factory(
        [
            make_submit_plan_response(trivial_plan(tool_name="gc_content")),
            make_tool_use_response("gc_content", {"sequence": "ATGCATGC"}),
            make_text_response("50%"),
        ]
    )

    captured: list[AgentStep] = []

    async def collector(step: AgentStep) -> None:
        captured.append(step)

    result = await run_agent(
        "GC of ATGC",
        project_id=DEFAULT_PROJECT_ID,
        llm=llm,
        on_step=collector,
    )

    # Every step that ended up in result.steps was also seen by the callback, in order.
    assert [s.type for s in captured] == [s.type for s in result.steps]
    assert [s.idx for s in captured] == [s.idx for s in result.steps]
    assert captured[0].type == "plan"
    assert any(s.type == "tool_call" and s.tool_name == "gc_content" for s in captured)


async def test_on_step_swallows_callback_errors(
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    trivial_plan,
) -> None:
    """A broken consumer (e.g. closed SSE connection) must not abort the agent run."""
    llm = fake_llm_factory(
        [
            make_submit_plan_response(trivial_plan(tool_name="gc_content")),
            make_tool_use_response("gc_content", {"sequence": "ATGCATGC"}),
            make_text_response("50%"),
        ]
    )

    async def broken(_step: AgentStep) -> None:
        raise RuntimeError("client went away")

    result = await run_agent(
        "GC of ATGC",
        project_id=DEFAULT_PROJECT_ID,
        llm=llm,
        on_step=broken,
    )
    # Run completed despite the callback raising every time.
    assert result.status == "completed"


async def test_on_step_fires_for_approval_request_step(
    fake_llm_factory, make_submit_plan_response, multi_step_plan
) -> None:
    """The inline `approval_requested` step (synthesized by run_agent, not by a sub-
    function) must also flow through the callback."""
    llm = fake_llm_factory([make_submit_plan_response(multi_step_plan([("blast", "Search NCBI.")]))])
    captured: list[AgentStep] = []

    async def collector(step: AgentStep) -> None:
        captured.append(step)

    result = await run_agent(
        "BLAST ATGCATGCATGCATGC",
        project_id=DEFAULT_PROJECT_ID,
        llm=llm,
        on_step=collector,
    )

    assert result.status == "pending_approval"
    types = [s.type for s in captured]
    assert types == ["plan", "approval_requested"]


async def test_on_step_fires_in_resume_agent(
    fake_llm_factory,
    make_tool_use_response,
    make_text_response,
    make_submit_verdict_response,
    passing_verdict,
) -> None:
    """resume_agent (the post-approval path) must also stream."""
    plan = Plan(
        is_trivial=False,
        summary="rev comp then gc",
        steps=[
            PlanStep(idx=0, description="rc", expected_tool="reverse_complement", rationale="x"),
            PlanStep(idx=1, description="gc", expected_tool="gc_content", rationale="y"),
        ],
    )
    llm = fake_llm_factory(
        [
            make_tool_use_response("reverse_complement", {"sequence": "ATGCATGC"}),
            make_tool_use_response("gc_content", {"sequence": "GCATGCAT"}),
            make_text_response("GC of reverse complement: 50%."),
            make_submit_verdict_response(passing_verdict()),
        ]
    )
    captured: list[AgentStep] = []

    async def collector(step: AgentStep) -> None:
        captured.append(step)

    await resume_agent(
        goal="x",
        plan=plan,
        project_id=DEFAULT_PROJECT_ID,
        step_idx_start=2,
        llm=llm,
        on_step=collector,
    )
    tool_calls = [s for s in captured if s.type == "tool_call"]
    assert [s.tool_name for s in tool_calls] == ["reverse_complement", "gc_content"]
    assert any(s.type == "critique" for s in captured)


# --- Unit: SSE format helpers --------------------------------------------------------


def test_format_event_renders_dict_as_json() -> None:
    raw = format_event("step", {"idx": 0, "type": "plan"})
    assert raw == 'event: step\ndata: {"idx": 0, "type": "plan"}\n\n'


def test_format_event_renders_string_data_verbatim() -> None:
    raw = format_event("error", "boom")
    assert raw == "event: error\ndata: boom\n\n"


def test_format_event_splits_multiline_strings_into_data_lines() -> None:
    raw = format_event("note", "line1\nline2")
    assert raw == "event: note\ndata: line1\ndata: line2\n\n"


def test_format_keepalive_is_comment_line() -> None:
    assert format_keepalive() == ": keepalive\n\n"


# --- Integration: SSE endpoints via in-process FastAPI ------------------------------


# test_session_maker + streaming_client are shared fixtures defined in conftest.py.


_SSE_BLOCK_RE = re.compile(r"event: (\S+)\n((?:data: .*\n)+)\n", re.MULTILINE)


def _parse_sse_blocks(raw: str) -> list[tuple[str, dict | str]]:
    """Parse a stream's accumulated text into (event_name, parsed_data) tuples.
    Ignores comment lines (`: keepalive`)."""
    blocks = []
    for match in _SSE_BLOCK_RE.finditer(raw):
        event_name = match.group(1)
        data_lines = [line[len("data: ") :] for line in match.group(2).strip().split("\n")]
        payload = "\n".join(data_lines)
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            parsed = payload
        blocks.append((event_name, parsed))
    return blocks


async def test_sse_run_stream_emits_step_and_done(
    streaming_client,
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    trivial_plan,
) -> None:
    from bioforge.main import app

    llm = fake_llm_factory(
        [
            make_submit_plan_response(trivial_plan(tool_name="gc_content")),
            make_tool_use_response("gc_content", {"sequence": "ATGCATGC"}),
            make_text_response("GC content of ATGCATGC is 50%."),
        ]
    )
    app.dependency_overrides[get_llm] = lambda: llm

    raw = ""
    async with streaming_client.stream(
        "POST",
        "/agent/run/stream",
        json={"goal": "GC of ATGCATGC"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        async for chunk in response.aiter_text():
            raw += chunk

    events = _parse_sse_blocks(raw)
    event_names = [name for name, _ in events]
    assert event_names[0] == "step"
    assert event_names[-1] == "done"

    # Every emitted step block was a dict with idx + type.
    step_payloads = [data for name, data in events if name == "step"]
    assert all(isinstance(d, dict) and "idx" in d and "type" in d for d in step_payloads)

    step_types_in_order = [d["type"] for d in step_payloads]
    assert step_types_in_order[0] == "plan"
    assert "tool_call" in step_types_in_order
    assert "final" in step_types_in_order
    assert step_types_in_order[-1] == "validation"  # grounding validation step (default annotate)

    done = [data for name, data in events if name == "done"][0]
    assert done["status"] == "completed"
    assert done["trace_id"]
    assert "50" in done["response_text"]


async def test_sse_approve_stream_cancel_path(
    streaming_client,
    fake_llm_factory,
    make_submit_plan_response,
    multi_step_plan,
) -> None:
    """1) /agent/run/stream pauses for approval on a BLAST plan.
    2) /agent/{trace_id}/approve/stream with approved=false cancels.
    Two requests, same backing trace."""
    from bioforge.main import app

    plan_llm = fake_llm_factory([make_submit_plan_response(multi_step_plan([("blast", "Search NCBI nt.")]))])
    app.dependency_overrides[get_llm] = lambda: plan_llm

    # 1. Start the stream — should pause on approval.
    raw1 = ""
    async with streaming_client.stream(
        "POST",
        "/agent/run/stream",
        json={"goal": "BLAST ATGCATGCATGCATGCATGC against nt"},
    ) as response:
        async for chunk in response.aiter_text():
            raw1 += chunk

    events1 = _parse_sse_blocks(raw1)
    done1 = [d for n, d in events1 if n == "done"][0]
    assert done1["status"] == "pending_approval"
    assert done1["pending_plan"] is not None
    trace_id = done1["trace_id"]

    # 2. Cancel via /approve/stream. The LLM is never called again.
    cancel_llm = fake_llm_factory([])
    app.dependency_overrides[get_llm] = lambda: cancel_llm

    raw2 = ""
    async with streaming_client.stream(
        "POST",
        f"/agent/{trace_id}/approve/stream",
        json={"approved": False, "reason": "too expensive for now"},
    ) as response:
        async for chunk in response.aiter_text():
            raw2 += chunk

    events2 = _parse_sse_blocks(raw2)
    types_seen = [data.get("type") for name, data in events2 if name == "step"]
    assert "approval_decision" in types_seen
    done2 = [d for n, d in events2 if n == "done"][0]
    assert done2["status"] == "cancelled"
    # No LLM call burned to cancel.
    assert cancel_llm.calls == []
