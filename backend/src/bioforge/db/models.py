from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
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
    # Owner (users.id). Nullable for back-compat: legacy projects predate accounts and are
    # backfilled to the default user by the auth migration. Every creation path sets it, so in
    # practice it is always populated; queries scope by it when auth is enabled. Plain indexed
    # string (no DB FK), matching how Trace.project_id references projects.
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
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
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    # "agent" — written by the remember tool. "user" — written via PATCH endpoint.
    # "system" — bootstrapped (e.g., the default project's organism).
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="agent")
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
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

    # --- Durable job model (Celery phase) ---
    # Which queue executed this run ("inline" | "celery"): provenance, and it drives how the
    # frontend cancels (disconnect-cancel vs Celery revoke).
    job_backend: Mapped[str] = mapped_column(String(16), nullable=False, default="inline")
    # The Celery task id when job_backend == "celery"; null for inline runs.
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # When the worker actually began executing -- distinct from created_at (enqueued) and
    # completed_at (finished). Null while the job is still queued.
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (Index("ix_traces_project_created", "project_id", "created_at"),)


class UploadedFile(Base):
    """A user-uploaded data file (the registry row). The bytes live in the storage adapter under
    `storage_key`; this row is the catalog the API + the agent list, look up, and reference. Scoped
    to a project (same isolation boundary as Trace/ProjectMemory)."""

    __tablename__ = "uploaded_files"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)  # the original name, as uploaded
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)  # key within the storage adapter
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)  # content hash (provenance + dedupe signal)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (Index("ix_uploaded_files_project_created", "project_id", "created_at"),)


class User(Base):
    """An account. The unit of ownership + isolation once auth is enabled. When auth is OFF the
    single bootstrapped default user owns everything, so the rest of the code never special-cases
    "no user" -- there is always a current user."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    # argon2id hash. The default user carries a non-verifiable sentinel so it can never be
    # logged into -- it is an ownership identity, not an account.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class Prediction(Base):
    """One recorded platform prediction + (later) its measured wet-lab outcome -- the feedback loop.

    The loop (Limitation #4): the platform records a prediction for some subject (a guide RNA, a
    variant, a sample), the user runs the experiment, then records the MEASURED outcome here. Once
    enough predictions carry an outcome, the platform recomputes agreement / calibration over the
    matched pairs (reusing benchmarks.reliability + benchmarks.calibration) -- so the displayed
    confidence is grounded in the user's own results, not just published numbers.

    A single table: `observed_value` is null until the result comes in (closing the loop is just an
    UPDATE). `kind` drives which agreement curve is computed -- "probability" (-> calibration, the
    outcome must be 0/1) or "regression" (-> ranking reliability, any float)."""

    __tablename__ = "predictions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # What was predicted. The join key between a prediction and its measured outcome (e.g. a guide
    # sequence, a variant key, a sample id). Free-form; the user defines the namespace.
    subject_key: Mapped[str] = mapped_column(String(255), nullable=False)
    # Human label for the assay/quantity (e.g. "on-target efficiency", "P(pathogenic)").
    assay: Mapped[str] = mapped_column(String(120), nullable=False)
    # "probability" (outcome in {0,1} -> calibration) or "regression" (any float -> ranking).
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="regression")
    predicted_value: Mapped[float] = mapped_column(Float, nullable=False)
    # Provenance: which tool/model produced the prediction (e.g. "score_guide_on_target deepcrispr").
    source: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # The measured wet-lab outcome. Null until the user records it -- this nullability IS the loop.
    observed_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Optional note recorded with the outcome (e.g. replicate count, conditions).
    outcome_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (Index("ix_predictions_project_created", "project_id", "created_at"),)


class PipelineJob(Base):
    """One nf-core pipeline run. Tracks status + streams events like a Trace row, but for
    long-running Nextflow executions rather than agent conversations. Project-scoped."""

    __tablename__ = "pipeline_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    pipeline: Mapped[str] = mapped_column(String(120), nullable=False)  # e.g. "nf-core/rnaseq"
    revision: Mapped[str] = mapped_column(String(40), nullable=False)  # pinned version tag
    profile: Mapped[str] = mapped_column(String(120), nullable=False, default="test")
    samplesheet: Mapped[str | None] = mapped_column(Text, nullable=True)  # CSV text, written at submit time
    params_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # extra --param key=val JSON

    # queued / running / completed / failed / cancelled
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")

    celery_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    nextflow_run_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Append-only event log persisted as a JSON array.
    # Each element: {seq, type, step_name|null, payload|null, ts (ISO-8601)}
    events: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_pipeline_jobs_project_created", "project_id", "created_at"),)


class AuthSession(Base):
    """A login session. We store only the SHA-256 of the bearer token, never the token itself, so a
    database leak cannot be replayed as a live login. Lookups hash the presented token and match."""

    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)  # sha256 hex
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
