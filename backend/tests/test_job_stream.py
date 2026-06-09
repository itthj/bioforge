"""Durable-job progress stream (Celery phase, slice 4): GET /agent/{id}/stream.

Polls the Trace and relays new steps as SSE until terminal. Covered hermetically:
  - CATCH-UP: a finished run streams its persisted steps + a terminal `done`, immediately.
  - LIVE: a job whose steps are committed by another connection over time is followed to
    completion, every step emitted once and in order (simulates the worker writing concurrently).
  - STALENESS: a job stuck non-terminal past the worker time limit is reported honestly (an
    `error` + the real non-terminal status), never a fabricated `completed`.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime

from bioforge.constants import DEFAULT_PROJECT_ID
from bioforge.db.models import Trace
from sqlalchemy import select

_SSE_BLOCK_RE = re.compile(r"event: (\S+)\n((?:data: .*\n)+)\n", re.MULTILINE)


def _parse_sse(raw: str) -> list[tuple[str, dict | str]]:
    blocks: list[tuple[str, dict | str]] = []
    for match in _SSE_BLOCK_RE.finditer(raw):
        name = match.group(1)
        payload = "\n".join(line[len("data: ") :] for line in match.group(2).strip().split("\n"))
        try:
            parsed: dict | str = json.loads(payload)
        except json.JSONDecodeError:
            parsed = payload
        blocks.append((name, parsed))
    return blocks


def _step(idx: int, type_: str) -> dict:
    return {"idx": idx, "type": type_, "duration_ms": 0}


async def test_stream_catches_up_a_finished_trace(streaming_client, test_session_maker) -> None:
    async with test_session_maker() as s:
        trace = Trace(
            project_id=DEFAULT_PROJECT_ID,
            goal="GC of ATGC",
            status="completed",
            model="claude-sonnet-4-6",
            response_text="GC content is 50%.",
            job_backend="celery",
            steps=[_step(0, "plan"), _step(1, "tool_call"), _step(2, "final")],
        )
        s.add(trace)
        await s.commit()
        trace_id = trace.id

    raw = ""
    async with streaming_client.stream("GET", f"/agent/{trace_id}/stream") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        async for chunk in resp.aiter_text():
            raw += chunk

    events = _parse_sse(raw)
    names = [n for n, _ in events]
    assert names.count("step") == 3
    assert names[-1] == "done"
    done = next(d for n, d in events if n == "done")
    assert done["status"] == "completed"
    assert "50" in done["response_text"]


async def test_stream_follows_a_live_job(streaming_client, test_session_maker, monkeypatch) -> None:
    """A second connection commits steps over time then flips the status terminal -- exactly what
    the Celery worker does in another process. The stream must follow it to completion, emitting
    every step once and in idx order."""
    import bioforge.api.agent as agent_api

    monkeypatch.setattr(agent_api, "_STREAM_POLL_SECONDS", 0.05)

    async with test_session_maker() as s:
        trace = Trace(
            project_id=DEFAULT_PROJECT_ID,
            goal="multi-step run",
            status="running",
            model="claude-sonnet-4-6",
            response_text="",
            job_backend="celery",
            started_at=datetime.now(UTC),
            steps=[_step(0, "plan")],
        )
        s.add(trace)
        await s.commit()
        trace_id = trace.id

    async def producer() -> None:
        for i in (1, 2, 3):
            await asyncio.sleep(0.08)
            async with test_session_maker() as s:
                row = (await s.execute(select(Trace).where(Trace.id == trace_id))).scalar_one()
                row.steps = list(row.steps) + [_step(i, "tool_call")]
                await s.commit()
        await asyncio.sleep(0.08)
        async with test_session_maker() as s:
            row = (await s.execute(select(Trace).where(Trace.id == trace_id))).scalar_one()
            row.steps = list(row.steps) + [_step(4, "final")]
            row.status = "completed"
            row.response_text = "all done."
            await s.commit()

    parts: list[str] = []

    async def consumer() -> None:
        async with streaming_client.stream("GET", f"/agent/{trace_id}/stream") as resp:
            async for chunk in resp.aiter_text():
                parts.append(chunk)

    await asyncio.gather(consumer(), producer())

    events = _parse_sse("".join(parts))
    step_idxs = [d["idx"] for n, d in events if n == "step"]
    assert step_idxs == [0, 1, 2, 3, 4]  # every step once, in order, no dupes
    done = next(d for n, d in events if n == "done")
    assert done["status"] == "completed"
    assert done["response_text"] == "all done."


async def test_stream_reports_staleness_for_a_dead_worker(streaming_client, test_session_maker, monkeypatch) -> None:
    """A job stuck `running` past the worker time limit (a lost worker that never wrote a terminal
    state) is reported honestly -- an error plus the real non-terminal status, not a fake done."""
    import bioforge.api.agent as agent_api
    from bioforge.config import settings

    monkeypatch.setattr(agent_api, "_STREAM_POLL_SECONDS", 0.03)
    monkeypatch.setattr(agent_api, "_STREAM_STALE_MARGIN_SECONDS", 0.1)
    monkeypatch.setattr(settings, "celery_task_time_limit", 0)

    async with test_session_maker() as s:
        trace = Trace(
            project_id=DEFAULT_PROJECT_ID,
            goal="stuck run",
            status="running",
            model="claude-sonnet-4-6",
            job_backend="celery",
            started_at=datetime.now(UTC),
            steps=[_step(0, "plan")],
        )
        s.add(trace)
        await s.commit()
        trace_id = trace.id

    raw = ""
    async with streaming_client.stream("GET", f"/agent/{trace_id}/stream") as resp:
        async for chunk in resp.aiter_text():
            raw += chunk

    events = _parse_sse(raw)
    names = [n for n, _ in events]
    assert "error" in names
    done = next(d for n, d in events if n == "done")
    assert done["status"] == "running"  # honest -- never fabricated completed


async def test_stream_missing_trace_emits_error(streaming_client) -> None:
    raw = ""
    async with streaming_client.stream("GET", "/agent/nope-not-here/stream") as resp:
        assert resp.status_code == 200  # SSE channel opens, then carries an error event
        async for chunk in resp.aiter_text():
            raw += chunk
    events = _parse_sse(raw)
    assert any(n == "error" for n, _ in events)
