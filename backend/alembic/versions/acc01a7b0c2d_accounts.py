"""accounts: users + auth_sessions tables, projects.user_id

Revision ID: acc01a7b0c2d
Revises: a1c0ffee9b1d
Create Date: 2026-06-09 20:10:00.000000

Adds the accounts layer (Phase 6): a `users` table, an `auth_sessions` table (bearer-token
sessions, storing only the token's SHA-256), and an owner column `projects.user_id`.

Back-compatible: seeds the non-loginable default user and backfills every existing project to it,
so legacy single-user data keeps working unchanged. `projects.user_id` is nullable for the same
reason. Values are hardcoded (not imported from app constants) so this historical migration stays
deterministic regardless of future code changes.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "acc01a7b0c2d"
down_revision: str | None = "a1c0ffee9b1d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_USER_ID = "default-user"
_DEFAULT_USER_EMAIL = "default@bioforge.local"
_NON_VERIFIABLE_HASH = "!"  # never verifies -> the default user can't be logged into
_SEED_TS = datetime(2026, 6, 9, tzinfo=UTC)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_token_hash", "auth_sessions", ["token_hash"], unique=True)

    op.add_column("projects", sa.Column("user_id", sa.String(length=64), nullable=True))
    op.create_index("ix_projects_user_id", "projects", ["user_id"])

    # Seed the default user, then backfill every existing project to it.
    users_table = sa.table(
        "users",
        sa.column("id", sa.String),
        sa.column("email", sa.String),
        sa.column("password_hash", sa.String),
        sa.column("display_name", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )
    op.bulk_insert(
        users_table,
        [
            {
                "id": _DEFAULT_USER_ID,
                "email": _DEFAULT_USER_EMAIL,
                "password_hash": _NON_VERIFIABLE_HASH,
                "display_name": "Default user",
                "is_active": True,
                "created_at": _SEED_TS,
                "updated_at": _SEED_TS,
            }
        ],
    )
    op.execute(f"UPDATE projects SET user_id = '{_DEFAULT_USER_ID}' WHERE user_id IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_index("ix_projects_user_id")
        batch.drop_column("user_id")
    op.drop_index("ix_auth_sessions_token_hash", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_id", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
