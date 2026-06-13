"""predictions table (Limitation #4 - wet-lab feedback loop)

Revision ID: c2e1a7d4f8b3
Revises: b1f09c3e8a21
Create Date: 2026-06-13 00:30:00.000000

Additive: adds predictions. No existing tables are altered.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2e1a7d4f8b3"
down_revision: str | None = "b1f09c3e8a21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "predictions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(64), nullable=False),
        sa.Column("subject_key", sa.String(255), nullable=False),
        sa.Column("assay", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="regression"),
        sa.Column("predicted_value", sa.Float, nullable=False),
        sa.Column("source", sa.String(120), nullable=True),
        sa.Column("observed_value", sa.Float, nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome_note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_predictions_project_created", "predictions", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_predictions_project_created", table_name="predictions")
    op.drop_table("predictions")
