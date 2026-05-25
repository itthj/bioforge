"""Alembic environment for BioForge.

Sync runner. Migrations run at FastAPI startup via `asyncio.to_thread`, and developers
invoke `alembic upgrade head` directly from `backend/`. Either path uses this same env.

The URL is sourced from BIOFORGE_DB_URL (matching the Settings convention) with the
async driver suffix stripped — alembic is sync. If BIOFORGE_DB_URL is unset, falls
back to the [alembic] sqlalchemy.url value in alembic.ini.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Ensure `bioforge.*` imports resolve when alembic runs from backend/.
_BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))

# Import models so every table registers on Base.metadata BEFORE autogenerate inspects it.
from bioforge.db import models  # noqa: F401, E402
from bioforge.db.engine import Base  # noqa: E402

config = context.config

# Configure logging from alembic.ini if it has [loggers] sections.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# URL precedence: env var > alembic.ini default. The env var path is what FastAPI
# startup and production deploys use; alembic.ini is a dev fallback.
env_url = os.environ.get("BIOFORGE_DB_URL")
if env_url:
    # Strip async driver — alembic's sync runner can't use aiosqlite / asyncpg.
    sync_url = env_url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg")
    config.set_main_option("sqlalchemy.url", sync_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit migration SQL without an active DB connection.

    Used by `alembic upgrade head --sql > migration.sql` for review pipelines.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite-specific: render batch ops for ALTER TABLE compatibility.
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live DB connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite needs render_as_batch for non-trivial ALTERs to work; harmless for
            # other dialects.
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
