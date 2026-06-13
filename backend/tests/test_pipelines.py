"""Tests for Limitation #5: nf-core pipeline jobs.

Covers:
  - PipelineJob model creation / DB round-trip (hermetic, no real nextflow).
  - runner.py: build_nextflow_argv correctness; run_pipeline with injected runner.
  - api/pipelines.py: POST/GET/DELETE endpoints; SSE stream catch-up.
  - Feature flag enforcement.

No real `nextflow` binary is required.  The injected subprocess runner writes a
synthetic trace and returns immediately, so the full async path is exercised without
I/O side-effects.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from httpx import AsyncClient

from bioforge.db.models import PipelineJob
from bioforge.pipelines.runner import (
    SUPPORTED_PIPELINES,
    PipelineEvent,
    _SubprocessResult,
    build_nextflow_argv,
    run_pipeline,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def nf_enabled(monkeypatch):
    """Enable the nextflow feature flag for tests that exercise the runner."""
    monkeypatch.setenv("BIOFORGE_NEXTFLOW_ENABLED", "true")


# ---------------------------------------------------------------------------
# runner.build_nextflow_argv
# ---------------------------------------------------------------------------


def test_build_argv_contains_revision_and_profile(tmp_path: Path) -> None:
    samplesheet = tmp_path / "samples.csv"
    samplesheet.write_text("sample,fastq_1\nS1,file.fastq.gz\n")
    argv = build_nextflow_argv(
        pipeline="nf-core/rnaseq",
        revision="3.14.0",
        profile="test,docker",
        samplesheet_path=samplesheet,
        outdir=tmp_path / "results",
        trace_path=tmp_path / "trace.txt",
        run_name="test-run",
    )
    assert argv[0] == "nextflow"
    assert argv[1] == "run"
    assert argv[2] == "nf-core/rnaseq"
    assert "--revision" in argv
    assert "3.14.0" in argv
    assert "-profile" in argv
    assert "test,docker" in argv
    assert "--input" in argv
    assert "-with-trace" in argv
    assert "-name" in argv and "test-run" in argv


def test_build_argv_extra_params(tmp_path: Path) -> None:
    samplesheet = tmp_path / "s.csv"
    samplesheet.write_text("")
    argv = build_nextflow_argv(
        pipeline="nf-core/sarek",
        revision="3.4.4",
        profile="test",
        samplesheet_path=samplesheet,
        outdir=tmp_path / "out",
        trace_path=tmp_path / "trace.txt",
        run_name="r",
        extra_params={"genome": "GRCh38", "tools": "haplotypecaller"},
    )
    # Extra params appear as --key value pairs.
    assert "--genome" in argv and "GRCh38" in argv
    assert "--tools" in argv and "haplotypecaller" in argv


# ---------------------------------------------------------------------------
# runner.run_pipeline -- injected subprocess runner
# ---------------------------------------------------------------------------


def _make_fake_runner(*, exit_code: int = 0, trace_rows: list[dict] | None = None):
    """Return a subprocess runner that writes a synthetic trace and exits."""

    async def runner(argv: list[str], work_dir: Path) -> _SubprocessResult:
        trace_path = Path(argv[argv.index("-with-trace") + 1])
        rows = trace_rows or []
        lines = ["task_id\tname\tstatus\texit"]
        for i, row in enumerate(rows, start=1):
            lines.append(f"{i}\t{row['name']}\t{row['status']}\t{row.get('exit', '0')}")
        trace_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        await asyncio.sleep(0)
        return _SubprocessResult(returncode=exit_code)

    return runner


@pytest.mark.asyncio
async def test_run_pipeline_success_emits_events(nf_enabled, tmp_path: Path) -> None:
    events: list[PipelineEvent] = []

    fake_runner = _make_fake_runner(
        trace_rows=[
            {"name": "NFCORE_RNASEQ:FASTQC", "status": "COMPLETED"},
            {"name": "NFCORE_RNASEQ:STAR_ALIGN", "status": "COMPLETED"},
        ]
    )

    status, error = await run_pipeline(
        job_id="test-job-id",
        pipeline="nf-core/rnaseq",
        revision="3.14.0",
        profile="test",
        samplesheet_csv="sample,fastq_1\nS1,/tmp/s.fastq.gz\n",
        work_dir=tmp_path,
        subprocess_runner=fake_runner,
        on_event=lambda ev: events.append(ev),
    )

    assert status == "completed"
    assert error is None
    types = [e.type for e in events]
    assert "run_started" in types
    assert "run_completed" in types
    step_completed = [e for e in events if e.type == "step_completed"]
    assert len(step_completed) == 2


@pytest.mark.asyncio
async def test_run_pipeline_failure_emits_run_failed(nf_enabled, tmp_path: Path) -> None:
    events: list[PipelineEvent] = []
    fake_runner = _make_fake_runner(exit_code=1, trace_rows=[{"name": "BAD", "status": "FAILED", "exit": "1"}])

    status, error = await run_pipeline(
        job_id="fail-job",
        pipeline="nf-core/rnaseq",
        revision="3.14.0",
        profile="test",
        samplesheet_csv="sample,fastq_1\nS1,/tmp/s.fastq.gz\n",
        work_dir=tmp_path,
        subprocess_runner=fake_runner,
        on_event=lambda ev: events.append(ev),
    )

    assert status == "failed"
    assert error is not None and "exit 1" in error
    assert any(e.type == "run_failed" for e in events)
    assert any(e.type == "step_failed" for e in events)


@pytest.mark.asyncio
async def test_run_pipeline_rejects_without_flag(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("BIOFORGE_NEXTFLOW_ENABLED", raising=False)
    with pytest.raises(RuntimeError, match="BIOFORGE_NEXTFLOW_ENABLED"):
        await run_pipeline(
            job_id="x",
            pipeline="nf-core/rnaseq",
            revision="3.14.0",
            profile="test",
            samplesheet_csv="",
            work_dir=tmp_path,
        )


@pytest.mark.asyncio
async def test_run_pipeline_event_callback_receives_seq(nf_enabled, tmp_path: Path) -> None:
    collected: list[int] = []

    async def cb(ev: PipelineEvent) -> None:
        collected.append(ev.seq)

    fake_runner = _make_fake_runner()
    await run_pipeline(
        job_id="seq-test",
        pipeline="nf-core/rnaseq",
        revision="3.14.0",
        profile="test",
        samplesheet_csv="",
        work_dir=tmp_path,
        subprocess_runner=fake_runner,
        on_event=cb,
    )
    # seq must be monotonically increasing.
    assert collected == list(range(len(collected)))


def test_supported_pipelines_catalogue() -> None:
    """All supported pipelines have non-empty version tags."""
    for pipeline, rev in SUPPORTED_PIPELINES.items():
        assert pipeline.startswith("nf-core/"), f"Expected nf-core/ prefix: {pipeline}"
        assert rev, f"Empty revision for {pipeline}"


# ---------------------------------------------------------------------------
# API endpoints -- hermetic HTTP tests (no subprocess, no celery)
# Fixtures: `streaming_client` (httpx + app), `test_session_maker` (DB).
# The default project is pre-seeded by streaming_client; its id comes from constants.
# ---------------------------------------------------------------------------

from bioforge.constants import DEFAULT_PROJECT_ID


@pytest.mark.asyncio
async def test_post_pipeline_queued_inline(streaming_client: AsyncClient, monkeypatch) -> None:
    """POST /pipelines in inline mode with nextflow flag unset: job created, runner
    raises, job ends in 'failed' status gracefully (no crash, no faked result)."""
    monkeypatch.delenv("BIOFORGE_NEXTFLOW_ENABLED", raising=False)
    resp = await streaming_client.post(
        "/pipelines",
        json={
            "project_id": DEFAULT_PROJECT_ID,
            "pipeline": "nf-core/rnaseq",
            "profile": "test",
            "samplesheet": "sample,fastq_1\nS1,/tmp/r1.fastq.gz\n",
        },
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["pipeline"] == "nf-core/rnaseq"
    # Without the flag the inline runner raises -> job status is failed.
    assert body["status"] == "failed"


@pytest.mark.asyncio
async def test_post_pipeline_unsupported_pipeline(streaming_client: AsyncClient) -> None:
    resp = await streaming_client.post(
        "/pipelines",
        json={
            "project_id": DEFAULT_PROJECT_ID,
            "pipeline": "nf-core/not-a-real-pipeline",
            "profile": "test",
            "samplesheet": "x\n",
        },
    )
    assert resp.status_code == 422
    assert "Supported" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_get_pipeline_list(streaming_client: AsyncClient, test_session_maker) -> None:
    from datetime import UTC, datetime

    async with test_session_maker() as session:
        job = PipelineJob(
            project_id=DEFAULT_PROJECT_ID,
            pipeline="nf-core/rnaseq",
            revision="3.14.0",
            profile="test",
            samplesheet="",
            status="completed",
            events=[],
            created_at=datetime.now(UTC),
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    resp = await streaming_client.get(f"/pipelines?project_id={DEFAULT_PROJECT_ID}")
    assert resp.status_code == 200
    ids = [j["id"] for j in resp.json()]
    assert job_id in ids


@pytest.mark.asyncio
async def test_get_pipeline_detail(streaming_client: AsyncClient, test_session_maker) -> None:
    from datetime import UTC, datetime

    async with test_session_maker() as session:
        job = PipelineJob(
            project_id=DEFAULT_PROJECT_ID,
            pipeline="nf-core/sarek",
            revision="3.4.4",
            profile="test",
            samplesheet="",
            status="running",
            events=[{"seq": 0, "type": "run_started", "step_name": None, "payload": None, "ts": "2026-01-01T00:00:00"}],
            created_at=datetime.now(UTC),
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    resp = await streaming_client.get(f"/pipelines/{job_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == job_id
    assert len(body["events"]) == 1
    assert body["events"][0]["type"] == "run_started"


@pytest.mark.asyncio
async def test_get_pipeline_not_found(streaming_client: AsyncClient) -> None:
    resp = await streaming_client.get("/pipelines/does-not-exist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_pipeline_cancels(streaming_client: AsyncClient, test_session_maker) -> None:
    from datetime import UTC, datetime

    async with test_session_maker() as session:
        job = PipelineJob(
            project_id=DEFAULT_PROJECT_ID,
            pipeline="nf-core/rnaseq",
            revision="3.14.0",
            profile="test",
            samplesheet="",
            status="running",
            events=[],
            created_at=datetime.now(UTC),
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    resp = await streaming_client.delete(f"/pipelines/{job_id}")
    assert resp.status_code == 204

    async with test_session_maker() as session:
        updated = await session.get(PipelineJob, job_id)
        assert updated is not None
        assert updated.status == "cancelled"


@pytest.mark.asyncio
async def test_catalogue_endpoint(streaming_client: AsyncClient) -> None:
    resp = await streaming_client.get("/pipelines/catalogue/supported")
    assert resp.status_code == 200
    data = resp.json()
    assert "nf-core/rnaseq" in data
    assert "nf-core/sarek" in data


@pytest.mark.asyncio
async def test_stream_terminal_job_ends_immediately(streaming_client: AsyncClient, test_session_maker) -> None:
    """GET /pipelines/{id}/stream on a completed job: catch-up events then done."""
    from datetime import UTC, datetime

    events_data = [
        {"seq": 0, "type": "run_started", "step_name": None, "payload": None, "ts": "2026-01-01T00:00:00"},
        {"seq": 1, "type": "run_completed", "step_name": None, "payload": None, "ts": "2026-01-01T00:01:00"},
    ]
    async with test_session_maker() as session:
        job = PipelineJob(
            project_id=DEFAULT_PROJECT_ID,
            pipeline="nf-core/rnaseq",
            revision="3.14.0",
            profile="test",
            samplesheet="",
            status="completed",
            events=events_data,
            created_at=datetime.now(UTC),
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    async with streaming_client.stream("GET", f"/pipelines/{job_id}/stream") as resp:
        assert resp.status_code == 200
        chunks: list[str] = []
        async for chunk in resp.aiter_text():
            chunks.append(chunk)
            if "event: done" in chunk:
                break

    full = "".join(chunks)
    assert "event: event" in full
    assert "run_started" in full
    assert "event: done" in full
    assert "completed" in full
