"""uploaded files registry

Revision ID: facc01117a2b
Revises: acc01a7b0c2d
Create Date: 2026-06-09 20:40:00.000000

Adds the `uploaded_files` registry (Phase 6): one row per user-uploaded data file, cataloguing the
bytes stored in the storage adapter. Project-scoped, additive -- no change to existing tables.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "facc01117a2b"
down_revision: str | None = "acc01a7b0c2d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "uploaded_files",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_uploaded_files_project_id", "uploaded_files", ["project_id"])
    op.create_index("ix_uploaded_files_project_created", "uploaded_files", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_uploaded_files_project_created", table_name="uploaded_files")
    op.drop_index("ix_uploaded_files_project_id", table_name="uploaded_files")
    op.drop_table("uploaded_files")
