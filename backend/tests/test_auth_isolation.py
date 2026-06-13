"""Per-user isolation (Phase 6, slice 2).

With auth ON, every project + its traces/memory belong to the user who created them. Another user
gets 404 (never 403 -- we don't reveal that someone else's project exists). With auth OFF the
checks are no-ops, so the existing single-user behavior is unchanged (covered by the rest of the
suite, which all runs auth-off).
"""

from __future__ import annotations

from datetime import UTC, datetime

from bioforge.config import settings
from bioforge.db.models import Trace
from sqlalchemy import select


async def _auth_header(client, email: str, password: str = "a-strong-password-1") -> dict[str, str]:
    await client.post("/auth/register", json={"email": email, "password": password})
    token = (await client.post("/auth/login", json={"email": email, "password": password})).json()["token"]
    return {"Authorization": f"Bearer {token}"}


async def test_projects_are_isolated_per_user(streaming_client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True)
    alice = await _auth_header(streaming_client, "alice@lab.org")
    bob = await _auth_header(streaming_client, "bob@lab.org")

    created = await streaming_client.post(
        "/projects", json={"id": "alice-screen", "name": "Alice's screen"}, headers=alice
    )
    assert created.status_code == 201

    # Alice sees + can open her project; Bob cannot.
    assert any(p["id"] == "alice-screen" for p in (await streaming_client.get("/projects", headers=alice)).json())
    assert all(p["id"] != "alice-screen" for p in (await streaming_client.get("/projects", headers=bob)).json())

    assert (await streaming_client.get("/projects/alice-screen", headers=alice)).status_code == 200
    assert (await streaming_client.get("/projects/alice-screen", headers=bob)).status_code == 404
    assert (
        await streaming_client.patch("/projects/alice-screen", json={"name": "hijack"}, headers=bob)
    ).status_code == 404
    assert (await streaming_client.delete("/projects/alice-screen", headers=bob)).status_code == 404
    # Bob can't read or write Alice's memory either.
    assert (await streaming_client.get("/projects/alice-screen/memory", headers=bob)).status_code == 404
    assert (
        await streaming_client.put("/projects/alice-screen/memory/k", json={"value": "x", "kind": "fact"}, headers=bob)
    ).status_code == 404


async def test_traces_are_isolated_per_user(streaming_client, test_session_maker, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True)
    alice = await _auth_header(streaming_client, "alice2@lab.org")
    bob = await _auth_header(streaming_client, "bob2@lab.org")

    await streaming_client.post("/projects", json={"id": "alice-proj", "name": "A"}, headers=alice)

    # A run that happened in Alice's project.
    async with test_session_maker() as s:
        trace = Trace(
            project_id="alice-proj",
            goal="GC of ATGC",
            status="completed",
            model="claude-sonnet-4-6",
            response_text="done",
            steps=[{"idx": 0, "type": "final", "duration_ms": 0}],
            completed_at=datetime.now(UTC),
        )
        s.add(trace)
        await s.commit()
        trace_id = trace.id

    # Alice can read her run + its provenance; Bob gets 404 on every trace surface.
    assert (await streaming_client.get(f"/traces/{trace_id}", headers=alice)).status_code == 200
    assert (await streaming_client.get(f"/traces/{trace_id}", headers=bob)).status_code == 404
    assert (await streaming_client.get(f"/traces/{trace_id}/manifest", headers=bob)).status_code == 404
    assert (await streaming_client.get(f"/traces/{trace_id}/report", headers=bob)).status_code == 404
    assert (await streaming_client.get(f"/traces/{trace_id}/script", headers=bob)).status_code == 404
    assert (await streaming_client.get("/projects/alice-proj/traces", headers=bob)).status_code == 404
    assert (await streaming_client.get("/projects/alice-proj/traces", headers=alice)).status_code == 200


async def test_cannot_run_in_someone_elses_project(streaming_client, monkeypatch) -> None:
    from bioforge.api.agent import get_llm
    from bioforge.main import app

    monkeypatch.setattr(settings, "auth_enabled", True)
    app.dependency_overrides[get_llm] = lambda: object()  # request is rejected before the LLM is used
    alice = await _auth_header(streaming_client, "alice3@lab.org")
    bob = await _auth_header(streaming_client, "bob3@lab.org")
    await streaming_client.post("/projects", json={"id": "a3", "name": "A3"}, headers=alice)

    # Bob can't launch a run scoped to Alice's project.
    resp = await streaming_client.post("/agent/run", json={"goal": "hi", "project_id": "a3"}, headers=bob)
    assert resp.status_code == 404


async def test_unauthenticated_run_is_rejected_when_auth_on(streaming_client, monkeypatch) -> None:
    from bioforge.api.agent import get_llm
    from bioforge.main import app

    monkeypatch.setattr(settings, "auth_enabled", True)
    app.dependency_overrides[get_llm] = lambda: object()
    # No Authorization header at all -> 401 from get_current_user before any work happens.
    resp = await streaming_client.post("/agent/run", json={"goal": "hi", "project_id": "default-project"})
    assert resp.status_code == 401


async def test_new_project_records_owner(streaming_client, test_session_maker, monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_enabled", True)
    alice = await _auth_header(streaming_client, "owner@lab.org")
    me = (await streaming_client.get("/auth/me", headers=alice)).json()
    await streaming_client.post("/projects", json={"id": "owned", "name": "Owned"}, headers=alice)

    async with test_session_maker() as s:
        from bioforge.db.models import Project

        project = (await s.execute(select(Project).where(Project.id == "owned"))).scalar_one()
        assert project.user_id == me["id"]
