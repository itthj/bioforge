"""Tests for the run-history list endpoint (P0) — GET /projects/{id}/traces."""

from __future__ import annotations

import pytest
from bioforge.constants import DEFAULT_PROJECT_ID
from bioforge.db.models import Trace

_RUNS = [
    ("Compute GC content of ATGCATGC", "completed"),
    ("BLAST the spike sequence against nt", "completed"),
    ("Design CRISPR guides for BRCA1", "critique_failed"),
]


@pytest.fixture
async def _seed_runs(test_session_maker):
    async with test_session_maker() as session:
        for i, (goal, status) in enumerate(_RUNS):
            session.add(
                Trace(
                    id=f"run-{i}",
                    project_id=DEFAULT_PROJECT_ID,
                    goal=goal,
                    response_text=f"answer number {i} " * 30,
                    status=status,
                    model="claude-sonnet-4-test",
                    cost_usd=0.01 * i,
                )
            )
        # A run in a different project must NOT leak into the list.
        session.add(
            Trace(
                id="other-proj-run",
                project_id="other-project",
                goal="something else entirely",
                response_text="x",
                status="completed",
                model="m",
            )
        )
        await session.commit()
    return None


async def test_list_returns_only_this_projects_runs(streaming_client, _seed_runs) -> None:
    resp = await streaming_client.get(f"/projects/{DEFAULT_PROJECT_ID}/traces")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    assert all(r["project_id"] == DEFAULT_PROJECT_ID for r in body)
    # Summary carries the fields the history list needs.
    first = body[0]
    assert {"trace_id", "goal", "status", "model", "cost_usd", "response_preview", "created_at"} <= set(first)
    assert first["response_preview"]  # non-empty, truncated preview


async def test_list_search_filters_by_goal(streaming_client, _seed_runs) -> None:
    resp = await streaming_client.get(f"/projects/{DEFAULT_PROJECT_ID}/traces", params={"q": "crispr"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert "CRISPR" in body[0]["goal"]


async def test_list_respects_limit(streaming_client, _seed_runs) -> None:
    resp = await streaming_client.get(f"/projects/{DEFAULT_PROJECT_ID}/traces", params={"limit": 2})
    assert resp.status_code == 200
    assert len(resp.json()) == 2


async def test_empty_project_returns_empty_list(streaming_client) -> None:
    resp = await streaming_client.get("/projects/nonexistent-project/traces")
    assert resp.status_code == 200
    assert resp.json() == []
