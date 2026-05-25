from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from bioforge.config import settings


class Base(DeclarativeBase):
    pass


_engine = create_async_engine(settings.db_url, echo=False, future=True)
session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


# `backend/alembic.ini` lives two directories above this file (src/bioforge/db → src/bioforge → src → backend).
_ALEMBIC_INI = Path(__file__).resolve().parents[3] / "alembic.ini"


def _run_alembic_upgrade_sync() -> None:
    """Synchronously run `alembic upgrade head`. Called via asyncio.to_thread from
    `init_db()`. Lives at module scope so the test suite can patch it if needed."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    # Force the URL onto alembic from settings, mirroring what env.py does when
    # BIOFORGE_DB_URL is set. Without this override the [alembic] default in
    # alembic.ini would win even when the app is configured against a different DB.
    sync_url = settings.db_url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg")
    cfg.set_main_option("sqlalchemy.url", sync_url)
    # env.py also reads BIOFORGE_DB_URL — keep that path consistent for any code that
    # invokes alembic CLI subprocess-style against the same process state.
    os.environ.setdefault("BIOFORGE_DB_URL", settings.db_url)
    command.upgrade(cfg, "head")


async def init_db() -> None:
    """Bring the database schema up to head via `alembic upgrade head`.

    Replaces the previous `Base.metadata.create_all` path. Idempotent — alembic
    tracks applied revisions in `alembic_version` and skips already-applied ones.
    Runs in a worker thread so the FastAPI startup hook (async) doesn't block on
    sync alembic internals.
    """
    # Import models for any side-effect Base.metadata registration in case anything
    # downstream (e.g. drift-detection tests) consults Base.metadata.
    from bioforge.db import models  # noqa: F401

    await asyncio.to_thread(_run_alembic_upgrade_sync)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yields an AsyncSession, commits on success, rolls back on error."""
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
