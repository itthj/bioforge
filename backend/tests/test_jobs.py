"""Durable job persistence (Celery phase, slice 2).

Proves create_queued_trace / the step sink / finalize_trace against the IN-PROCESS run path,
before any Celery worker exists: a run is persisted incrementally (so a polling reader could
watch it) and survives into a fresh session.
"""

from __future__ import annotations

from bioforge.agent import run_agent
from bioforge.agent.jobs import create_queued_trace, finalize_trace, make_step_persister
from bioforge.constants import DEFAULT_PROJECT_ID
from bioforge.db.models import Trace
from sqlalchemy import select


async def test_create_queued_trace_starts_queued(test_session_maker) -> None:
    async with test_session_maker() as session:
        trace = await create_queued_trace(session, goal="GC of ATGCATGC", project_id=DEFAULT_PROJECT_ID)
        assert trace.status == "queued"
        assert trace.job_backend == "inline"
        assert trace.task_id is None
        assert trace.started_at is None
        assert trace.steps == []


async def test_step_sink_streams_steps_and_finalize_persists(
    test_session_maker,
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    trivial_plan,
) -> None:
    """The sink flips queued->running (stamping started_at), accumulates steps as the agent
    runs, and finalize writes the terminal state -- all durable across a fresh session."""
    llm = fake_llm_factory(
        [
            make_submit_plan_response(trivial_plan(tool_name="gc_content")),
            make_tool_use_response("gc_content", {"sequence": "ATGCATGC"}),
            make_text_response("GC content is 50%."),
        ]
    )

    async with test_session_maker() as session:
        trace = await create_queued_trace(session, goal="GC of ATGCATGC", project_id=DEFAULT_PROJECT_ID)
        trace_id = trace.id

        result = await run_agent(
            "GC of ATGCATGC",
            project_id=DEFAULT_PROJECT_ID,
            llm=llm,
            on_step=make_step_persister(session, trace),
        )

        # The sink ran DURING execution: queued -> running, started_at stamped, steps accrued
        # incrementally (it saw every step run_agent produced).
        assert trace.status == "running"
        assert trace.started_at is not None
        assert len(trace.steps) == len(result.steps) >= 2

        await finalize_trace(session, trace, result)
        assert trace.status == result.status  # terminal (completed), no longer "running"
        assert trace.status != "running"
        assert trace.response_text == result.response_text
        await session.commit()

    # Durable: reload in a brand-new session — the whole run survived.
    async with test_session_maker() as session2:
        reloaded = (await session2.execute(select(Trace).where(Trace.id == trace_id))).scalar_one()
        assert reloaded.status == result.status
        assert reloaded.job_backend == "inline"
        assert reloaded.response_text == result.response_text
        assert len(reloaded.steps) == len(result.steps)
        # Step dicts round-tripped through JSON with their type tags intact.
        assert reloaded.steps[0]["type"] == "plan"


async def test_celery_backend_and_task_id_recorded(test_session_maker) -> None:
    """A job created for the celery backend records provenance (backend + task id)."""
    async with test_session_maker() as session:
        trace = await create_queued_trace(
            session,
            goal="BLAST something",
            project_id=DEFAULT_PROJECT_ID,
            backend="celery",
            task_id="celery-task-123",
        )
        assert trace.job_backend == "celery"
        assert trace.task_id == "celery-task-123"
