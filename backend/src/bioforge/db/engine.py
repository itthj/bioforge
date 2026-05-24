from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from bioforge.config import settings


class Base(DeclarativeBase):
    pass


_engine = create_async_engine(settings.db_url, echo=False, future=True)
_session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """Create all tables. Idempotent — safe to call on every startup."""
    # Import models so they're registered on Base.metadata before create_all runs.
    from bioforge.db import models  # noqa: F401

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields an AsyncSession, commits on success, rolls back on error."""
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
