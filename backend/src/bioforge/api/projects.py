"""Project CRUD + per-project memory inspection/editing.

A `Project` is a workspace. Every Trace, ProjectMemory entry, and (later) file object is
scoped to one project. The memory endpoints under `/projects/{id}/memory` let the user
audit and edit what the `remember` tool has written — this is the "memory must be
inspectable and editable" guarantee from the spec.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bioforge.db.engine import get_session
from bioforge.db.models import Project, ProjectMemory

router = APIRouter()

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_KIND_VALUES = ("fact", "preference", "summary", "file_reference")


class ProjectCreate(BaseModel):
    id: str = Field(
        min_length=1,
        max_length=64,
        description=(
            "URL-safe slug, lowercase letters / digits / single dashes. Becomes the "
            "permanent identifier — pick a name you can live with."
        ),
    )
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    organism: str | None = Field(default=None, max_length=80)
    reference_genome: str | None = Field(default=None, max_length=40)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    organism: str | None = Field(default=None, max_length=80)
    reference_genome: str | None = Field(default=None, max_length=40)


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: str | None
    organism: str | None
    reference_genome: str | None
    created_at: datetime
    updated_at: datetime


class MemoryEntry(BaseModel):
    key: str
    value: str
    kind: str
    source: str
    rationale: str | None
    created_at: datetime
    updated_at: datetime


class MemoryUpsert(BaseModel):
    value: str = Field(min_length=1, max_length=4000)
    kind: Literal["fact", "preference", "summary", "file_reference"] = "fact"
    rationale: str | None = Field(default=None, max_length=500)


def _validate_slug(slug: str) -> str:
    if not _SLUG_RE.match(slug):
        raise HTTPException(
            status_code=422,
            detail=(
                "Project id must be a URL-safe slug: lowercase letters, digits, and "
                "single dashes between segments. Example: 'crispr-screen-2026'."
            ),
        )
    return slug


def _to_project_response(p: Project) -> ProjectResponse:
    return ProjectResponse(
        id=p.id,
        name=p.name,
        description=p.description,
        organism=p.organism,
        reference_genome=p.reference_genome,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


def _to_memory_entry(m: ProjectMemory) -> MemoryEntry:
    return MemoryEntry(
        key=m.key,
        value=m.value,
        kind=m.kind,
        source=m.source,
        rationale=m.rationale,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


# --- Projects --------------------------------------------------------------------------


@router.post("/projects", response_model=ProjectResponse, status_code=201)
async def create_project(body: ProjectCreate, session: AsyncSession = Depends(get_session)) -> ProjectResponse:
    _validate_slug(body.id)
    project = Project(
        id=body.id,
        name=body.name,
        description=body.description,
        organism=body.organism,
        reference_genome=body.reference_genome,
    )
    session.add(project)
    try:
        await session.flush()
    except IntegrityError as e:
        raise HTTPException(
            status_code=409,
            detail=f"Project {body.id!r} already exists.",
        ) from e
    return _to_project_response(project)


@router.get("/projects", response_model=list[ProjectResponse])
async def list_projects(
    session: AsyncSession = Depends(get_session),
) -> list[ProjectResponse]:
    rows = (await session.execute(select(Project).order_by(Project.created_at.desc()))).scalars().all()
    return [_to_project_response(p) for p in rows]


@router.get("/projects/{project_id}", response_model=ProjectResponse)
async def get_project(project_id: str, session: AsyncSession = Depends(get_session)) -> ProjectResponse:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found")
    return _to_project_response(project)


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    body: ProjectUpdate,
    session: AsyncSession = Depends(get_session),
) -> ProjectResponse:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found")
    if body.name is not None:
        project.name = body.name
    if body.description is not None:
        project.description = body.description
    if body.organism is not None:
        project.organism = body.organism
    if body.reference_genome is not None:
        project.reference_genome = body.reference_genome
    project.updated_at = datetime.now(UTC)
    await session.flush()
    return _to_project_response(project)


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(project_id: str, session: AsyncSession = Depends(get_session)) -> None:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found")
    await session.delete(project)
    await session.flush()


# --- Memory ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/memory", response_model=list[MemoryEntry])
async def list_memory(project_id: str, session: AsyncSession = Depends(get_session)) -> list[MemoryEntry]:
    # Confirm the project exists so the response distinguishes "no memory" from "no project".
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found")
    rows = (
        (
            await session.execute(
                select(ProjectMemory)
                .where(ProjectMemory.project_id == project_id)
                .order_by(ProjectMemory.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_to_memory_entry(m) for m in rows]


@router.put(
    "/projects/{project_id}/memory/{key}",
    response_model=MemoryEntry,
)
async def upsert_memory(
    project_id: str,
    key: str,
    body: MemoryUpsert,
    session: AsyncSession = Depends(get_session),
) -> MemoryEntry:
    """User-driven upsert. `source` is set to `user` so the audit trail distinguishes
    human edits from agent writes."""
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found")
    existing = (
        await session.execute(
            select(ProjectMemory).where(ProjectMemory.project_id == project_id, ProjectMemory.key == key)
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.value = body.value
        existing.kind = body.kind
        existing.rationale = body.rationale
        existing.source = "user"
        existing.updated_at = datetime.now(UTC)
        await session.flush()
        return _to_memory_entry(existing)

    new = ProjectMemory(
        project_id=project_id,
        key=key,
        value=body.value,
        kind=body.kind,
        rationale=body.rationale,
        source="user",
    )
    session.add(new)
    await session.flush()
    return _to_memory_entry(new)


@router.delete("/projects/{project_id}/memory/{key}", status_code=204)
async def delete_memory(project_id: str, key: str, session: AsyncSession = Depends(get_session)) -> None:
    row = (
        await session.execute(
            select(ProjectMemory).where(ProjectMemory.project_id == project_id, ProjectMemory.key == key)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Memory entry {key!r} not found in project {project_id!r}",
        )
    await session.delete(row)
    await session.flush()
