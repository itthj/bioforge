"""Cost controls (Phase 6, Limitation #6): per-user spend/run accounting + the pre-run quota gate.

The gate is tested directly (enforce_run_quota) since the run path itself needs an LLM; the /usage
endpoint is smoke-tested over HTTP. Both controls are opt-in -- off by default = no-op.
"""

from __future__ import annotations

import pytest
from bioforge.api.usage import compute_usage, enforce_run_quota
from bioforge.config import settings
from bioforge.db.models import Project, Trace, User
from fastapi import HTTPException


async def _seed_user_with_traces(maker, *, user_id: str, costs: list[float]) -> User:
    async with maker() as s:
        user = User(id=user_id, email=f"{user_id}@x.org", password_hash="!", display_name=None)
        s.add(user)
        s.add(Project(id=f"proj-{user_id}", name="P", user_id=user_id))
        for c in costs:
            s.add(Trace(project_id=f"proj-{user_id}", goal="g", status="completed", model="m", cost_usd=c))
        await s.commit()
    # Return a detached-but-usable User for the enforce/compute calls (they only read .id).
    return User(id=user_id, email=f"{user_id}@x.org", password_hash="!", display_name=None)


async def test_compute_usage_sums_spend_and_counts_runs(test_session_maker) -> None:
    user = await _seed_user_with_traces(test_session_maker, user_id="u-calc", costs=[1.5, 0.5, 0.25])
    async with test_session_maker() as s:
        snap = await compute_usage(s, user)
    assert snap.spend_this_month_usd == pytest.approx(2.25)
    assert snap.runs_last_hour == 3


async def test_enforce_is_noop_when_disabled(test_session_maker, monkeypatch) -> None:
    monkeypatch.setattr(settings, "budget_enabled", False)
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    user = await _seed_user_with_traces(test_session_maker, user_id="u-off", costs=[100.0] * 5)
    async with test_session_maker() as s:
        await enforce_run_quota(s, user)  # must not raise


async def test_enforce_blocks_over_budget_402(test_session_maker, monkeypatch) -> None:
    monkeypatch.setattr(settings, "budget_enabled", True)
    monkeypatch.setattr(settings, "monthly_budget_usd", 1.0)
    user = await _seed_user_with_traces(test_session_maker, user_id="u-budget", costs=[0.6, 0.6])  # $1.20 > $1.00
    async with test_session_maker() as s:
        with pytest.raises(HTTPException) as exc:
            await enforce_run_quota(s, user)
    assert exc.value.status_code == 402


async def test_enforce_allows_under_budget(test_session_maker, monkeypatch) -> None:
    monkeypatch.setattr(settings, "budget_enabled", True)
    monkeypatch.setattr(settings, "monthly_budget_usd", 10.0)
    user = await _seed_user_with_traces(test_session_maker, user_id="u-under", costs=[0.6])
    async with test_session_maker() as s:
        await enforce_run_quota(s, user)  # under cap -> no raise


async def test_enforce_blocks_over_rate_429(test_session_maker, monkeypatch) -> None:
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_runs_per_hour", 2)
    user = await _seed_user_with_traces(test_session_maker, user_id="u-rate", costs=[0.0, 0.0, 0.0])
    async with test_session_maker() as s:
        with pytest.raises(HTTPException) as exc:
            await enforce_run_quota(s, user)
    assert exc.value.status_code == 429


async def test_usage_endpoint_returns_snapshot(streaming_client) -> None:
    resp = await streaming_client.get("/usage")
    assert resp.status_code == 200
    body = resp.json()
    assert "spend_this_month_usd" in body
    assert "runs_last_hour" in body
    assert body["budget_enabled"] is False  # default
