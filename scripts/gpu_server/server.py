"""Reference cloud-GPU endpoint for BioForge's HttpGpuBackend (Limitation #3).

Deploy this on a machine/service that HAS a GPU (a GPU VM, Modal, RunPod, a lab workstation),
then point BioForge at it:

    BIOFORGE_GPU_BACKEND=http
    BIOFORGE_GPU_ENDPOINT=https://your-host:8080
    BIOFORGE_GPU_API_KEY=<the same token you set as GPU_API_KEY below>

It implements exactly the contract in backend/src/bioforge/gpu/backend.py:

    POST /jobs        {"task": str, "inputs": {...}}  -> 200 {"job_id": str}
    GET  /jobs/{id}   -> 200 {"status": "queued"|"running"|"completed"|"failed",
                              "result": {...}?, "error": str?}

Jobs run in a background thread and progress queued -> running -> completed/failed, so the
BioForge side polls until terminal -- the realistic GPU shape.

  pip install fastapi uvicorn        # + torch etc. for your real model
  GPU_API_KEY=secret python server.py    # listens on :8080

PLUG YOUR MODEL IN at `run_task()`. The default handler is honest: it reports whether a CUDA GPU
is actually visible (via torch if installed) and echoes the task -- it never pretends to have run
a model it didn't. Replace it with real inference (load weights once at startup, dispatch by task).
"""

from __future__ import annotations

import os
import threading
import uuid
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

API_KEY = os.environ.get("GPU_API_KEY", "")  # empty = no auth (fine for a private network)

app = FastAPI(title="BioForge GPU endpoint (reference)")
_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


class SubmitBody(BaseModel):
    task: str
    inputs: dict[str, Any] = {}


def _check_auth(authorization: str | None) -> None:
    if not API_KEY:
        return
    if authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="bad or missing bearer token")


# ----------------------------------------------------------------------------------------
# PLUG YOUR MODEL HERE. Return a JSON-serializable dict. Raise to mark the job failed.
# Load heavy weights ONCE at module import (outside this function), then dispatch by task.
# ----------------------------------------------------------------------------------------
def run_task(task: str, inputs: dict[str, Any]) -> dict[str, Any]:
    gpu = _gpu_info()
    # Example dispatch -- replace each branch with real inference:
    #   if task == "boltz_structure": return run_boltz(inputs)
    #   if task == "deepcrispr":      return score_guides(inputs)
    return {
        "task": task,
        "echo_inputs": inputs,
        "gpu": gpu,
        "note": "Reference handler -- replace run_task() with your model inference.",
    }


def _gpu_info() -> dict[str, Any]:
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            return {"cuda": True, "device": torch.cuda.get_device_name(0), "count": torch.cuda.device_count()}
        return {"cuda": False, "reason": "torch present but no CUDA device visible"}
    except ImportError:
        return {"cuda": False, "reason": "torch not installed -- this reference runs on CPU"}


def _worker(job_id: str, task: str, inputs: dict[str, Any]) -> None:
    with _lock:
        _jobs[job_id]["status"] = "running"
    try:
        result = run_task(task, inputs)
        with _lock:
            _jobs[job_id].update(status="completed", result=result)
    except Exception as e:  # noqa: BLE001
        with _lock:
            _jobs[job_id].update(status="failed", error=f"{type(e).__name__}: {e}")


@app.post("/jobs")
def submit(body: SubmitBody, authorization: str | None = Header(default=None)) -> dict[str, str]:
    _check_auth(authorization)
    job_id = uuid.uuid4().hex
    with _lock:
        _jobs[job_id] = {"status": "queued", "result": None, "error": None}
    threading.Thread(target=_worker, args=(job_id, body.task, body.inputs), daemon=True).start()
    return {"job_id": job_id}


@app.get("/jobs/{job_id}")
def poll(job_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _check_auth(authorization)
    with _lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job_id")
    return {"status": job["status"], "result": job["result"], "error": job["error"]}


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "gpu": _gpu_info()}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
