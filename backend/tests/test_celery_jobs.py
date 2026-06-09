"""Durable run jobs (Celery phase, slice 3).

Three layers, all hermetic (no Redis, no real broker):
  1. The worker CORE (`run_agent_job_async`) against a tmp DB on settings.db_url -- proves a
     queued run executes, streams steps via the committing sink, and reaches a terminal state;
     and that a failure flips the job to `error` rather than hanging in `running`.
  2. The ENQUEUE wiring -- `POST /agent/run` in celery mode persists a `queued` trace, enqueues
     the run job, records the task id, and returns immediately (no in-request execution).
  3. END-TO-END through the real Celery task in eager mode -- enqueue -> task -> sync/async
     bridge -> worker core -> durable terminal trace, all in one process.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest_asyncio
from bioforge.agent.jobs import create_queued_trace, resume_agent_job_async, run_agent_job_async
from bioforge.api.agent import get_llm
from bioforge.config import settings
from bioforge.constants import DEFAULT_PROJECT_ID
from bioforge.db.engine import Base, get_session
from bioforge.db.models import Project, Trace
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def worker_db(tmp_path, monkeypatch):
    """A tmp on-disk SQLite pointed at by settings.db_url, with the schema + default project.

    On-disk (not :memory:) so the worker core -- which builds its OWN engine from settings.db_url
    -- and the test's assertion sessions, two separate connections, see each other's commits.
    Yields a sessionmaker for arranging rows + asserting final state.
    """
    db_url = f"sqlite+aiosqlite:///{tmp_path.as_posix()}/worker.db"
    monkeypatch.setattr(settings, "db_url", db_url)
    engine = create_async_engine(db_url, future=True)
    from bioforge.db import models  # noqa: F401  -- register tables on Base.metadata

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        s.add(Project(id=DEFAULT_PROJECT_ID, name="Default project (test)"))
        await s.commit()
    yield maker
    await engine.dispose()


# --- 1. Worker core -----------------------------------------------------------------


async def test_run_agent_job_async_runs_queued_to_terminal(
    worker_db,
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    trivial_plan,
) -> None:
    """A queued trace, handed to the worker core, executes and lands terminal -- with steps
    committed incrementally (queued->running, started_at stamped) and a final response."""
    async with worker_db() as session:
        trace = await create_queued_trace(
            session, goal="GC of ATGCATGC", project_id=DEFAULT_PROJECT_ID, backend="celery"
        )
        trace_id = trace.id
        await session.commit()

    llm = fake_llm_factory(
        [
            make_submit_plan_response(trivial_plan(tool_name="gc_content")),
            make_tool_use_response("gc_content", {"sequence": "ATGCATGC"}),
            make_text_response("GC content is 50%."),
        ]
    )

    out = await run_agent_job_async(trace_id=trace_id, goal="GC of ATGCATGC", project_id=DEFAULT_PROJECT_ID, llm=llm)
    assert out == {"trace_id": trace_id, "status": "completed"}

    # Durable: a brand-new connection sees the finished run.
    async with worker_db() as s2:
        reloaded = (await s2.execute(select(Trace).where(Trace.id == trace_id))).scalar_one()
        assert reloaded.status == "completed"
        assert reloaded.job_backend == "celery"
        assert reloaded.started_at is not None  # the committing sink flipped queued->running
        assert reloaded.response_text
        step_types = [s["type"] for s in reloaded.steps]
        assert step_types[0] == "plan"
        assert "tool_call" in step_types


async def test_run_agent_job_async_marks_error_on_failure(worker_db, monkeypatch) -> None:
    """If the run raises, the job is committed as `error` (never left hanging in `running`)."""

    async def _boom(*args: Any, **kwargs: Any):
        raise RuntimeError("planner exploded")

    # run_agent is lazily imported inside the core as `from bioforge.agent import run_agent`.
    monkeypatch.setattr("bioforge.agent.run_agent", _boom)

    async with worker_db() as session:
        trace = await create_queued_trace(session, goal="doomed run", project_id=DEFAULT_PROJECT_ID, backend="celery")
        trace_id = trace.id
        await session.commit()

    out = await run_agent_job_async(trace_id=trace_id, goal="doomed run", project_id=DEFAULT_PROJECT_ID)
    assert out["status"] == "error"

    async with worker_db() as s2:
        reloaded = (await s2.execute(select(Trace).where(Trace.id == trace_id))).scalar_one()
        assert reloaded.status == "error"
        assert "planner exploded" in reloaded.response_text


async def test_run_agent_job_async_missing_trace_is_reported(worker_db) -> None:
    out = await run_agent_job_async(trace_id="does-not-exist", goal="x", project_id=DEFAULT_PROJECT_ID)
    assert out["status"] == "error"


# --- 2. Enqueue wiring (no worker) --------------------------------------------------


async def test_agent_run_celery_mode_enqueues_and_returns_queued(
    streaming_client, test_session_maker, monkeypatch
) -> None:
    """POST /agent/run in celery mode returns immediately with status `queued`, enqueues the
    run job with the right args, and records the returned task id -- without executing in-request.

    `streaming_client` builds on `test_session_maker`; requesting both here yields the SAME maker,
    so we can read back the row the handler persisted."""
    from bioforge.main import app
    from bioforge.tasks import celery_app as celery_mod

    monkeypatch.setattr(settings, "task_queue", "celery")
    app.dependency_overrides[get_llm] = lambda: object()  # enqueue path never touches the LLM

    sent: dict[str, Any] = {}

    def fake_apply_async(args=None, **kwargs):  # noqa: ANN001
        sent["args"] = args
        return type("R", (), {"id": "celery-task-abc"})()

    monkeypatch.setattr(celery_mod.run_agent_job_task, "apply_async", fake_apply_async)

    resp = await streaming_client.post("/agent/run", json={"goal": "GC of ATGCATGC"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    assert body["steps"] == []
    trace_id = body["trace_id"]

    # Enqueued the per-run job with the run's parameters.
    assert sent["args"][0] == trace_id
    assert sent["args"][1] == "GC of ATGCATGC"

    # The trace row is persisted queued, celery-backed, with the task id recorded for cancel.
    async with test_session_maker() as s:
        row = (await s.execute(select(Trace).where(Trace.id == trace_id))).scalar_one()
        assert row.status == "queued"
        assert row.job_backend == "celery"
        assert row.task_id == "celery-task-abc"


# --- 3. End-to-end through the real Celery task (eager mode) -------------------------


@pytest_asyncio.fixture
async def celery_eager_client(tmp_path, monkeypatch):
    """httpx client where API + worker share ONE on-disk DB and the Celery app runs eagerly.

    Exercises the genuine path: POST enqueues -> the real `bioforge.tasks.run_agent_job` task
    fires in-process (eager) -> the sync/async bridge runs the worker core on settings.db_url ->
    the run lands durable. A fake LLM is injected by patching the loop's LLM factory (the task
    can't carry an unpicklable LLM across the broker)."""
    from bioforge.main import app
    from bioforge.tasks.celery_app import celery_app

    db_url = f"sqlite+aiosqlite:///{tmp_path.as_posix()}/eager.db"
    monkeypatch.setattr(settings, "db_url", db_url)
    monkeypatch.setattr(settings, "task_queue", "celery")
    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr(celery_app.conf, "task_eager_propagates", True)

    engine = create_async_engine(db_url, future=True)
    from bioforge.db import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        s.add(Project(id=DEFAULT_PROJECT_ID, name="Default project (test)"))
        await s.commit()

    async def override_get_session():
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_llm] = lambda: object()

    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, maker
    app.dependency_overrides.clear()
    await engine.dispose()


async def test_celery_eager_run_persists_terminal_trace(
    celery_eager_client,
    monkeypatch,
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    trivial_plan,
) -> None:
    client, maker = celery_eager_client

    fake = fake_llm_factory(
        [
            make_submit_plan_response(trivial_plan(tool_name="gc_content")),
            make_tool_use_response("gc_content", {"sequence": "ATGCATGC"}),
            make_text_response("GC content is 50%."),
        ]
    )
    # run_agent (in the worker thread) builds its LLM via loop.LLM(); hand it the fake.
    monkeypatch.setattr("bioforge.agent.loop.LLM", lambda: fake)

    resp = await client.post("/agent/run", json={"goal": "GC of ATGCATGC"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"  # the POST contract is enqueue-and-return
    trace_id = body["trace_id"]

    # Eager mode ran the task synchronously during enqueue, so the row is already terminal,
    # written by the worker core through its own engine on the shared DB.
    async with maker() as s:
        row = (await s.execute(select(Trace).where(Trace.id == trace_id))).scalar_one()
        assert row.status == "completed"
        assert row.job_backend == "celery"
        assert row.task_id is not None
        assert row.started_at is not None
        assert row.response_text
        assert any(st["type"] == "tool_call" for st in row.steps)


# --- 4. Cancellation (slice 5) ------------------------------------------------------


async def _make_trace(maker, *, status: str, backend: str, task_id: str | None) -> str:
    async with maker() as s:
        trace = Trace(
            project_id=DEFAULT_PROJECT_ID,
            goal="cancel me",
            status=status,
            model="claude-sonnet-4-6",
            job_backend=backend,
            task_id=task_id,
            steps=[{"idx": 0, "type": "plan", "duration_ms": 0}],
        )
        s.add(trace)
        await s.commit()
        return trace.id


async def test_cancel_revokes_celery_task_and_marks_cancelled(
    streaming_client, test_session_maker, monkeypatch
) -> None:
    from bioforge.tasks import celery_app as celery_mod

    revoked: dict[str, Any] = {}

    def fake_revoke(task_id, terminate=False, signal=None, **kwargs):  # noqa: ANN001
        revoked["task_id"] = task_id
        revoked["terminate"] = terminate
        revoked["signal"] = signal

    monkeypatch.setattr(celery_mod.celery_app.control, "revoke", fake_revoke)

    trace_id = await _make_trace(test_session_maker, status="running", backend="celery", task_id="task-xyz")

    resp = await streaming_client.post(f"/agent/{trace_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

    # The worker task was revoked with a hard terminate.
    assert revoked == {"task_id": "task-xyz", "terminate": True, "signal": "SIGTERM"}

    async with test_session_maker() as s:
        row = (await s.execute(select(Trace).where(Trace.id == trace_id))).scalar_one()
        assert row.status == "cancelled"
        assert row.response_text  # a human-readable note, not empty


async def test_cancel_inline_run_is_rejected(streaming_client, test_session_maker) -> None:
    trace_id = await _make_trace(test_session_maker, status="running", backend="inline", task_id=None)
    resp = await streaming_client.post(f"/agent/{trace_id}/cancel")
    assert resp.status_code == 409


async def test_cancel_terminal_run_is_rejected(streaming_client, test_session_maker) -> None:
    trace_id = await _make_trace(test_session_maker, status="completed", backend="celery", task_id="task-done")
    resp = await streaming_client.post(f"/agent/{trace_id}/cancel")
    assert resp.status_code == 409


async def test_cancel_missing_trace_is_404(streaming_client) -> None:
    resp = await streaming_client.post("/agent/nope/cancel")
    assert resp.status_code == 404


# --- 5. /agent/run/stream celery bridge (slice 5) -----------------------------------


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


async def test_run_stream_celery_emits_queued_then_streams_to_done(
    celery_eager_client,
    monkeypatch,
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    trivial_plan,
) -> None:
    """In celery mode the SSE entrypoint enqueues + streams: a `queued` event carrying the
    trace_id (so Stop can /cancel), then the worker's steps, then `done`."""
    client, _maker = celery_eager_client
    fake = fake_llm_factory(
        [
            make_submit_plan_response(trivial_plan(tool_name="gc_content")),
            make_tool_use_response("gc_content", {"sequence": "ATGCATGC"}),
            make_text_response("GC content is 50%."),
        ]
    )
    monkeypatch.setattr("bioforge.agent.loop.LLM", lambda: fake)

    raw = ""
    async with client.stream("POST", "/agent/run/stream", json={"goal": "GC of ATGCATGC"}) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        async for chunk in resp.aiter_text():
            raw += chunk

    events = _parse_sse(raw)
    names = [n for n, _ in events]
    assert names[0] == "queued"
    queued = events[0][1]
    assert queued["trace_id"] and queued["job_backend"] == "celery"
    assert "step" in names
    done = next(d for n, d in events if n == "done")
    assert done["status"] == "completed"
    assert done["trace_id"] == queued["trace_id"]


# --- 6. Approval resume as a durable job (slice 6) ----------------------------------


async def _make_pending_approval_trace(maker, plan_dict: dict) -> str:
    """A review-mode run paused at approval: plan + approval_requested steps, the proposed plan
    persisted, and some planning tokens already counted (so additive usage is observable)."""
    async with maker() as s:
        trace = Trace(
            project_id=DEFAULT_PROJECT_ID,
            goal="rc then gc",
            status="pending_approval",
            model="claude-sonnet-4-6",
            job_backend="celery",
            awaiting_approval_plan=plan_dict,
            approval_reasons=["review mode"],
            tokens_input=200,
            tokens_output=80,
            steps=[
                {"idx": 0, "type": "plan", "duration_ms": 0},
                {"idx": 1, "type": "approval_requested", "duration_ms": 0},
            ],
        )
        s.add(trace)
        await s.commit()
        return trace.id


async def test_resume_agent_job_async_appends_steps_and_adds_usage(
    worker_db,
    fake_llm_factory,
    make_tool_use_response,
    make_text_response,
    make_submit_verdict_response,
    passing_verdict,
    multi_step_plan,
) -> None:
    """The resume worker core continues from step_idx_start, appends the resumed steps onto the
    existing prefix (never rewriting it), and ADDS its usage to the planning tokens already there."""
    plan_dict = multi_step_plan([("reverse_complement", "rc"), ("gc_content", "gc")])
    trace_id = await _make_pending_approval_trace(worker_db, plan_dict)
    # Emulate what the enqueueing request does: append the decision step + flip to running.
    async with worker_db() as s:
        row = (await s.execute(select(Trace).where(Trace.id == trace_id))).scalar_one()
        row.steps = [*row.steps, {"idx": 2, "type": "approval_decision", "duration_ms": 0, "approved": True}]
        row.status = "running"
        await s.commit()

    fake = fake_llm_factory(
        [
            make_tool_use_response("reverse_complement", {"sequence": "ATGCATGC"}),
            make_tool_use_response("gc_content", {"sequence": "GCATGCAT"}),
            make_text_response("GC of reverse complement: 50%."),
            make_submit_verdict_response(passing_verdict()),
        ]
    )

    out = await resume_agent_job_async(trace_id=trace_id, plan=plan_dict, step_idx_start=3, llm=fake)
    assert out["status"] in ("completed", "completed_after_replan")

    async with worker_db() as s:
        row = (await s.execute(select(Trace).where(Trace.id == trace_id))).scalar_one()
        assert row.status in ("completed", "completed_after_replan")
        types = [st["type"] for st in row.steps]
        # Prefix preserved, resumed steps appended after it.
        assert types[:3] == ["plan", "approval_requested", "approval_decision"]
        assert "tool_call" in types[3:]
        assert row.tokens_input > 200  # added to the planning tokens, not overwritten
        assert row.response_text
        assert row.awaiting_approval_plan is None


async def test_approve_stream_celery_resumes_as_durable_job(
    celery_eager_client,
    monkeypatch,
    fake_llm_factory,
    make_tool_use_response,
    make_text_response,
    make_submit_verdict_response,
    passing_verdict,
    multi_step_plan,
) -> None:
    """Approving in celery mode enqueues the resume; the stream emits the decision + a queued
    event + the resumed steps + done, and does NOT re-send the plan/approval prefix."""
    client, maker = celery_eager_client
    plan_dict = multi_step_plan([("reverse_complement", "rc"), ("gc_content", "gc")])
    trace_id = await _make_pending_approval_trace(maker, plan_dict)

    fake = fake_llm_factory(
        [
            make_tool_use_response("reverse_complement", {"sequence": "ATGCATGC"}),
            make_tool_use_response("gc_content", {"sequence": "GCATGCAT"}),
            make_text_response("done."),
            make_submit_verdict_response(passing_verdict()),
        ]
    )
    monkeypatch.setattr("bioforge.agent.loop.LLM", lambda: fake)

    raw = ""
    async with client.stream("POST", f"/agent/{trace_id}/approve/stream", json={"approved": True}) as resp:
        assert resp.status_code == 200
        async for chunk in resp.aiter_text():
            raw += chunk

    events = _parse_sse(raw)
    names = [n for n, _ in events]
    step_types = [d.get("type") for n, d in events if n == "step" and isinstance(d, dict)]
    assert "queued" in names
    assert "approval_decision" in step_types
    assert "tool_call" in step_types
    # The prefix the client already has is not re-streamed (start_index skips it).
    assert "plan" not in step_types
    assert "approval_requested" not in step_types
    done = next(d for n, d in events if n == "done")
    assert done["status"] in ("completed", "completed_after_replan")


async def test_approve_sync_celery_enqueues_resume(
    streaming_client, test_session_maker, monkeypatch, multi_step_plan
) -> None:
    """The non-stream approve endpoint, in celery mode, persists the decision, flips to running,
    and enqueues the resume job with [trace_id, plan, step_idx_start] -- returning immediately."""
    from bioforge.main import app
    from bioforge.tasks import celery_app as celery_mod

    monkeypatch.setattr(settings, "task_queue", "celery")
    app.dependency_overrides[get_llm] = lambda: object()
    plan_dict = multi_step_plan([("gc_content", "gc")])
    trace_id = await _make_pending_approval_trace(test_session_maker, plan_dict)

    sent: dict[str, Any] = {}

    def fake_apply_async(args=None, **kwargs):  # noqa: ANN001
        sent["args"] = args
        return type("R", (), {"id": "resume-task-1"})()

    monkeypatch.setattr(celery_mod.resume_agent_job_task, "apply_async", fake_apply_async)

    resp = await streaming_client.post(f"/agent/{trace_id}/approve", json={"approved": True})
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"

    assert sent["args"][0] == trace_id
    assert sent["args"][1]["summary"]  # the approved plan dict
    assert sent["args"][2] == 3  # 2 prefix steps + the decision step -> resume starts at idx 3

    async with test_session_maker() as s:
        row = (await s.execute(select(Trace).where(Trace.id == trace_id))).scalar_one()
        assert row.status == "running"
        assert row.task_id == "resume-task-1"
        assert any(st["type"] == "approval_decision" for st in row.steps)
