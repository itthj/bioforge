"""pipeline_jobs table (Limitation #5 - nf-core pipelines)

Revision ID: b1f09c3e8a21
Revises: facc01117a2b
Create Date: 2026-06-13 00:00:00.000000

Additive: adds pipeline_jobs. No existing tables are altered.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1f09c3e8a21"
down_revision: str | None = "facc01117a2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pipeline_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("pipeline", sa.String(120), nullable=False),
        sa.Column("revision", sa.String(40), nullable=False),
        sa.Column("profile", sa.String(120), nullable=False, server_default="test"),
        sa.Column("samplesheet", sa.Text, nullable=True),
        sa.Column("params_json", sa.Text, nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("celery_task_id", sa.String(64), nullable=True),
        sa.Column("nextflow_run_name", sa.String(64), nullable=True),
        sa.Column("events", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_pipeline_jobs_project_created", "pipeline_jobs", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_pipeline_jobs_project_created", table_name="pipeline_jobs")
    op.drop_table("pipeline_jobs")
