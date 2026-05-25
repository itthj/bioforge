from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from bioforge.db.engine import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid.uuid4())


class Project(Base):
    """A user-defined workspace. Every Trace, ProjectMemory entry, and (eventually) file
    object is scoped to a Project. For Phase 1 there is exactly one user — the project
    boundary exists for memory isolation and for the multi-tenant retrofit later."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    organism: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reference_genome: Mapped[str | None] = mapped_column(String(40), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class ProjectMemory(Base):
    """A durable fact, preference, or summary the agent (or user) wants persisted.

    `(project_id, key)` is unique — writes by key are upserts. The agent calls the
    `remember` tool to write; the user can inspect, edit, or delete entries via the
    /projects/{id}/memory API. Provenance is recorded in `source`."""

    __tablename__ = "project_memory"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    # "agent" — written by the remember tool. "user" — written via PATCH endpoint.
    # "system" — bootstrapped (e.g., the default project's organism).
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="agent")
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    __table_args__ = (
        UniqueConstraint("project_id", "key", name="uq_project_memory_key"),
        Index("ix_project_memory_project_updated", "project_id", "updated_at"),
    )


class Trace(Base):
    """One persisted agent run. `project_id` is the primary isolation boundary from day one
    — every row carries it even though Phase 0 hardcodes a single project."""

    __tablename__ = "traces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    goal: Mapped[str] = mapped_column(Text, nullable=False)
    response_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)

    steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    tokens_input: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_output: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_cache_creation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_cache_read: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # When status == "pending_approval", this stores the plan awaiting user approval.
    # Cleared (set to null) once the run resumes via the /approve endpoint.
    awaiting_approval_plan: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    approval_reasons: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    __table_args__ = (Index("ix_traces_project_created", "project_id", "created_at"),)
