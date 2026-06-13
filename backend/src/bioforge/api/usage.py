"""Cost controls (Phase 6, Limitation #6): per-user spend + run counts, the `/usage` endpoint, and
the pre-run quota gate.

Spend and run counts are derived from the durable `Trace` rows (joined to the user's projects) -- no
new tables, and the numbers are exactly what the run history already shows. Both the budget and the
rate limit are opt-in (settings, default off), so single-user/local behavior is unchanged. The gate
is a PRE-check on spend-so-far: a run already in flight isn't interrupted, but the next one is
blocked once the cap is reached -- honest and simple.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bioforge.api.auth import get_current_user
from bioforge.config import settings
from bioforge.db.engine import get_session
from bioforge.db.models import Project, Trace, User

router = APIRouter()


class UsageSnapshot(BaseModel):
    spend_this_month_usd: float
    monthly_budget_usd: float = Field(description="0 means unlimited.")
    budget_enabled: bool
    runs_last_hour: int
    rate_limit_runs_per_hour: int
    rate_limit_enabled: bool


def _month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def _spend_this_month(session: AsyncSession, user_id: str, now: datetime) -> float:
    stmt = (
        select(func.coalesce(func.sum(Trace.cost_usd), 0.0))
        .select_from(Trace)
        .join(Project, Trace.project_id == Project.id)
        .where(Project.user_id == user_id, Trace.created_at >= _month_start(now))
    )
    return float((await session.execute(stmt)).scalar_one() or 0.0)


async def _runs_last_hour(session: AsyncSession, user_id: str, now: datetime) -> int:
    stmt = (
        select(func.count())
        .select_from(Trace)
        .join(Project, Trace.project_id == Project.id)
        .where(Project.user_id == user_id, Trace.created_at >= now - timedelta(hours=1))
    )
    return int((await session.execute(stmt)).scalar_one() or 0)


async def compute_usage(session: AsyncSession, user: User) -> UsageSnapshot:
    now = datetime.now(UTC)
    return UsageSnapshot(
        spend_this_month_usd=round(await _spend_this_month(session, user.id, now), 6),
        monthly_budget_usd=settings.monthly_budget_usd,
        budget_enabled=settings.budget_enabled,
        runs_last_hour=await _runs_last_hour(session, user.id, now),
        rate_limit_runs_per_hour=settings.rate_limit_runs_per_hour,
        rate_limit_enabled=settings.rate_limit_enabled,
    )


async def enforce_run_quota(session: AsyncSession, user: User) -> None:
    """Raise before a run starts if the current user is over their rate limit (429) or monthly
    budget (402). No-op unless the respective control is enabled."""
    now = datetime.now(UTC)
    if settings.rate_limit_enabled:
        runs = await _runs_last_hour(session, user.id, now)
        if runs >= settings.rate_limit_runs_per_hour:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Rate limit reached: {settings.rate_limit_runs_per_hour} runs/hour. "
                    "Please wait before starting another run."
                ),
            )
    if settings.budget_enabled and settings.monthly_budget_usd > 0:
        spend = await _spend_this_month(session, user.id, now)
        if spend >= settings.monthly_budget_usd:
            raise HTTPException(
                status_code=402,
                detail=(
                    f"Monthly budget of ${settings.monthly_budget_usd:.2f} reached "
                    f"(spent ${spend:.2f}). It resets at the start of next month."
                ),
            )


@router.get("/usage", response_model=UsageSnapshot)
async def get_usage(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> UsageSnapshot:
    """The current user's spend this month + runs this hour, with the configured limits."""
    return await compute_usage(session, current_user)
