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

from typing import Any

import pytest_asyncio
from bioforge.agent.jobs import create_queued_trace, run_agent_job_async
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
