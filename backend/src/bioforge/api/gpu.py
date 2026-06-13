"""Cloud-GPU execution path API (Limitation #3).

GET  /gpu/status   Honest capability report: which backend is configured (no secrets).
POST /gpu/submit   Run a GPU job through the configured backend. 503 (with the setup message) when
                   no backend is configured -- never a fabricated result.

The route is always mounted so the frontend can show GPU availability; whether a job can actually
run depends entirely on BIOFORGE_GPU_BACKEND (default "none" -> honest refusal).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from bioforge.api.auth import get_current_user
from bioforge.db.models import User
from bioforge.gpu.backend import (
    GpuBackendNotConfiguredError,
    GpuExecutionError,
    GpuJobSpec,
    get_gpu_backend,
    gpu_capability,
)

router = APIRouter()


class GpuStatus(BaseModel):
    backend: str = Field(description="Configured backend: 'none' or 'http'.")
    configured: bool = Field(description="True iff a GPU job can actually be dispatched.")
    endpoint_host: str = Field(description="Host of the GPU service (no scheme, no api key). Empty when none.")


class GpuSubmitRequest(BaseModel):
    task: str = Field(..., description="Model/operation to run on the GPU, e.g. 'boltz_structure'.")
    inputs: dict = Field(default_factory=dict, description="Task payload forwarded to the GPU service.")


class GpuSubmitResponse(BaseModel):
    status: str
    result: dict | None = None
    error: str | None = None
    remote_job_id: str | None = None


@router.get("/gpu/status", response_model=GpuStatus)
async def gpu_status(current_user: User = Depends(get_current_user)) -> GpuStatus:
    """Report GPU execution capability honestly (drives the frontend's GPU indicator)."""
    return GpuStatus(**gpu_capability())


@router.post("/gpu/submit", response_model=GpuSubmitResponse)
async def gpu_submit(
    body: GpuSubmitRequest,
    current_user: User = Depends(get_current_user),
) -> GpuSubmitResponse:
    """Dispatch a GPU job through the configured backend and return its terminal result.

    Synchronous: the HTTP backend does its own submit->poll loop. With no backend configured this
    returns 503 carrying the setup message -- the refusal is explicit, never a faked result."""
    backend = get_gpu_backend()
    try:
        result = await backend.run(GpuJobSpec(task=body.task, inputs=body.inputs))
    except GpuBackendNotConfiguredError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except GpuExecutionError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return GpuSubmitResponse(
        status=result.status,
        result=result.result,
        error=result.error,
        remote_job_id=result.remote_job_id,
    )
