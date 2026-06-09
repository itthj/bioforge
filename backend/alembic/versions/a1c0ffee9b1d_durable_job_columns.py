"""add durable job columns to traces

Revision ID: a1c0ffee9b1d
Revises: 69190266b045
Create Date: 2026-06-04 22:40:00.000000

Adds the durable job-model columns (Celery phase): job_backend, task_id, started_at.
Additive + back-compatible: job_backend NOT NULL with a server_default so existing rows
backfill to "inline"; the other two are nullable.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1c0ffee9b1d"
down_revision: str | None = "69190266b045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NOT NULL with a server_default so the column backfills cleanly on tables with existing
    # rows (and matches the model's nullable=False, which the migration drift test enforces).
    op.add_column(
        "traces",
        sa.Column("job_backend", sa.String(length=16), nullable=False, server_default="inline"),
    )
    op.add_column("traces", sa.Column("task_id", sa.String(length=64), nullable=True))
    op.add_column("traces", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    # batch_alter_table so DROP COLUMN works on SQLite too (table-rebuild under the hood).
    with op.batch_alter_table("traces") as batch:
        batch.drop_column("started_at")
        batch.drop_column("task_id")
        batch.drop_column("job_backend")
