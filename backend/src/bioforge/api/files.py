"""File / dataset upload API (Phase 6, slice 3).

Wires the (already-built, project-isolated) storage adapter to owner-checked endpoints + the
`uploaded_files` registry, so a user can bring their own data -- FASTA, VCF, result tables -- into a
project. The agent reads these in slice 4. Endpoints live under a project and require ownership.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bioforge.api.auth import get_current_user, require_owned_project
from bioforge.config import settings
from bioforge.db.engine import get_session
from bioforge.db.models import UploadedFile, User
from bioforge.storage.adapter import get_storage

router = APIRouter()

_CHUNK = 1024 * 1024  # 1 MiB read chunks


class UploadedFileResponse(BaseModel):
    id: str
    project_id: str
    filename: str
    content_type: str | None
    size_bytes: int
    sha256: str = Field(description="SHA-256 of the content -- provenance + a dedupe signal.")
    created_at: datetime


def _to_response(row: UploadedFile) -> UploadedFileResponse:
    return UploadedFileResponse(
        id=row.id,
        project_id=row.project_id,
        filename=row.filename,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        created_at=row.created_at,
    )


def _allowed_extensions() -> set[str]:
    return {e.strip().lower() for e in settings.upload_allowed_extensions.split(",") if e.strip()}


def _check_extension(filename: str) -> None:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    allowed = _allowed_extensions()
    if ext not in allowed:
        raise HTTPException(
            status_code=415,
            detail=f"File type {('.' + ext) if ext else '(none)'} is not accepted. Allowed: {sorted(allowed)}.",
        )


def _safe_filename(name: str) -> str:
    """A download-safe filename for the Content-Disposition header (no path, no quotes)."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", name).strip("_") or "download"


async def _read_capped(file: UploadFile) -> bytes:
    """Read the upload in chunks, refusing once it crosses the configured size cap -- so a huge
    upload is rejected without buffering the whole thing first."""
    buf = bytearray()
    while chunk := await file.read(_CHUNK):
        buf.extend(chunk)
        if len(buf) > settings.upload_max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds the {settings.upload_max_bytes}-byte upload limit.",
            )
    return bytes(buf)


@router.post("/projects/{project_id}/files", response_model=UploadedFileResponse, status_code=201)
async def upload_file(
    project_id: str,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> UploadedFileResponse:
    await require_owned_project(session, project_id, current_user)
    filename = (file.filename or "upload").strip()
    _check_extension(filename)
    data = await _read_capped(file)
    if not data:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")

    # Generate the id up front (the model's column default only fires at flush) so it can key the
    # storage object too: <project>/uploads/<file_id>.
    file_id = str(uuid.uuid4())
    storage_key = f"uploads/{file_id}"
    storage = get_storage()
    meta = await asyncio.to_thread(
        storage.put, project_id=project_id, key=storage_key, data=data, content_type=file.content_type
    )
    row = UploadedFile(
        id=file_id,
        project_id=project_id,
        filename=filename,
        storage_key=storage_key,
        content_type=file.content_type,
        size_bytes=meta.size_bytes,
        sha256=meta.sha256,
    )
    session.add(row)
    await session.flush()
    return _to_response(row)


@router.get("/projects/{project_id}/files", response_model=list[UploadedFileResponse])
async def list_files(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[UploadedFileResponse]:
    await require_owned_project(session, project_id, current_user)
    rows = (
        (
            await session.execute(
                select(UploadedFile)
                .where(UploadedFile.project_id == project_id)
                .order_by(UploadedFile.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_to_response(r) for r in rows]


async def _load_file_or_404(session: AsyncSession, project_id: str, file_id: str) -> UploadedFile:
    row = await session.get(UploadedFile, file_id)
    if row is None or row.project_id != project_id:
        raise HTTPException(status_code=404, detail=f"File {file_id!r} not found in project {project_id!r}")
    return row


@router.get("/projects/{project_id}/files/{file_id}")
async def download_file(
    project_id: str,
    file_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> Response:
    await require_owned_project(session, project_id, current_user)
    row = await _load_file_or_404(session, project_id, file_id)
    storage = get_storage()
    data = await asyncio.to_thread(storage.get, project_id=project_id, key=row.storage_key)
    return Response(
        content=data,
        media_type=row.content_type or "application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{_safe_filename(row.filename)}"'},
    )


@router.delete("/projects/{project_id}/files/{file_id}", status_code=204)
async def delete_file(
    project_id: str,
    file_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    await require_owned_project(session, project_id, current_user)
    row = await _load_file_or_404(session, project_id, file_id)
    storage = get_storage()
    await asyncio.to_thread(storage.delete, project_id=project_id, key=row.storage_key)
    await session.delete(row)
    await session.flush()
