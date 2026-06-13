"""Cloud-GPU execution path (Limitation #3).

Always-on GPU compute is a hardware/budget decision, not a code one -- BioForge does not ship
GPUs. What IS buildable is an *execution path*: a provider-agnostic way to dispatch a
GPU-requiring job (a deep model inference, a structure prediction) to a GPU endpoint the user
provisions, and stream the result back.

Two backends, selected by `settings.gpu_backend`:
  * **NullGpuBackend** (default, "none"). There is no GPU. It REFUSES every submission with a clear
    setup message. It never fabricates a result -- the whole point of the integrity posture: a tool
    that cannot run says so rather than inventing an answer.
  * **HttpGpuBackend** ("http"). Provider-agnostic. POSTs the job to `gpu_endpoint` (bearer
    `gpu_api_key`), then polls `{endpoint}/jobs/{id}` until terminal. Works against ANY compliant
    HTTP GPU service -- a self-hosted GPU server, a Modal / RunPod / Replicate web endpoint -- so
    the user wires it to whatever GPU they are paying for without a code change here.

The HTTP contract (minimal, documented so a user can implement the other side):
    POST   {endpoint}/jobs        {"task": str, "inputs": {...}}  -> 200 {"job_id": str}
    GET    {endpoint}/jobs/{id}   -> 200 {"status": "queued"|"running"|"completed"|"failed",
                                          "result": {...}?, "error": str?}

Testability: HttpGpuBackend takes an injected async HTTP client (any object exposing async
`post`/`get` returning an httpx-like response), so the submit->poll->result loop is tested without
a network or a real GPU.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from bioforge.config import settings


@dataclass
class GpuJobSpec:
    """What to run on the GPU. `task` names the model/operation; `inputs` is its payload."""

    task: str
    inputs: dict[str, Any] = field(default_factory=dict)


@dataclass
class GpuJobResult:
    """Terminal outcome of a GPU job."""

    status: str  # "completed" | "failed"
    result: dict[str, Any] | None = None
    error: str | None = None
    remote_job_id: str | None = None


class GpuBackendNotConfiguredError(RuntimeError):
    """Raised when a GPU job is submitted but no GPU backend is configured. Honest refusal."""


class GpuExecutionError(RuntimeError):
    """Raised when a configured GPU backend fails to run the job (network, remote error, timeout)."""


class GpuBackend(Protocol):
    """The execution-path contract. `name` is for honest capability reporting."""

    name: str

    async def run(self, spec: GpuJobSpec) -> GpuJobResult: ...


class NullGpuBackend:
    """Default backend: there is no GPU, so refuse honestly. Never returns a fabricated result."""

    name = "none"

    async def run(self, spec: GpuJobSpec) -> GpuJobResult:
        raise GpuBackendNotConfiguredError(
            "No GPU backend is configured (BIOFORGE_GPU_BACKEND=none). Always-on GPU is a "
            "hardware/budget decision; to enable a cloud-GPU execution path, set "
            "BIOFORGE_GPU_BACKEND=http and BIOFORGE_GPU_ENDPOINT to a GPU service you provision "
            "(self-hosted, Modal, RunPod, Replicate, ...). Until then GPU-requiring work is "
            "unavailable -- it is not silently faked."
        )


# Minimal structural type for the injected HTTP client (httpx.AsyncClient satisfies it).
class _AsyncHttpClient(Protocol):
    async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str]) -> Any: ...
    async def get(self, url: str, *, headers: dict[str, str]) -> Any: ...


class HttpGpuBackend:
    """Provider-agnostic HTTP GPU backend: submit a job, poll until terminal, return the result.

    `endpoint` is the base URL of the GPU service; `api_key` (optional) becomes a Bearer token.
    `client_factory` builds the async HTTP client per call (defaults to httpx.AsyncClient); tests
    inject a fake. Polling cadence + overall timeout come from settings unless overridden.
    """

    name = "http"

    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str = "",
        timeout_seconds: float | None = None,
        poll_seconds: float | None = None,
        client_factory=None,
    ) -> None:
        if not endpoint:
            raise GpuBackendNotConfiguredError(
                "BIOFORGE_GPU_BACKEND=http requires BIOFORGE_GPU_ENDPOINT (the GPU service base URL)."
            )
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds if timeout_seconds is not None else settings.gpu_timeout_seconds
        self.poll_seconds = poll_seconds if poll_seconds is not None else settings.gpu_poll_seconds
        self._client_factory = client_factory

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _make_client(self):
        if self._client_factory is not None:
            return self._client_factory()
        import httpx

        return httpx.AsyncClient(timeout=30.0)

    async def run(self, spec: GpuJobSpec) -> GpuJobResult:
        client = self._make_client()
        # The injected fakes in tests are plain objects; real httpx clients are context managers.
        is_ctx = hasattr(client, "__aenter__")
        if is_ctx:
            client = await client.__aenter__()
        try:
            submit_resp = await client.post(
                f"{self.endpoint}/jobs",
                json={"task": spec.task, "inputs": spec.inputs},
                headers=self._headers(),
            )
            _raise_for_status(submit_resp, "submit")
            remote_id = submit_resp.json().get("job_id")
            if not remote_id:
                raise GpuExecutionError("GPU service did not return a job_id on submit.")

            start = time.monotonic()
            while True:
                if time.monotonic() - start > self.timeout_seconds:
                    raise GpuExecutionError(f"GPU job {remote_id} did not finish within {self.timeout_seconds:.0f}s.")
                poll_resp = await client.get(f"{self.endpoint}/jobs/{remote_id}", headers=self._headers())
                _raise_for_status(poll_resp, "poll")
                body = poll_resp.json()
                status = body.get("status")
                if status == "completed":
                    return GpuJobResult(status="completed", result=body.get("result"), remote_job_id=remote_id)
                if status == "failed":
                    return GpuJobResult(
                        status="failed",
                        error=body.get("error") or "GPU job reported failure without detail.",
                        remote_job_id=remote_id,
                    )
                await asyncio.sleep(self.poll_seconds)
        finally:
            if is_ctx:
                await client.__aexit__(None, None, None)


def _raise_for_status(resp: Any, phase: str) -> None:
    code = getattr(resp, "status_code", 200)
    if code >= 400:
        body = ""
        try:
            body = resp.text
        except Exception:  # noqa: BLE001
            pass
        raise GpuExecutionError(f"GPU service returned HTTP {code} on {phase}: {body[:300]}")


def get_gpu_backend() -> GpuBackend:
    """Return the configured GPU backend. `none` -> NullGpuBackend (honest refusal); `http` ->
    HttpGpuBackend built from settings. An unknown value raises rather than silently no-op'ing."""
    backend = settings.gpu_backend.lower()
    if backend == "none":
        return NullGpuBackend()
    if backend == "http":
        return HttpGpuBackend(endpoint=settings.gpu_endpoint, api_key=settings.gpu_api_key)
    raise GpuBackendNotConfiguredError(f"Unknown BIOFORGE_GPU_BACKEND={settings.gpu_backend!r}. Supported: none, http.")


def gpu_capability() -> dict[str, Any]:
    """Honest capability report for the frontend: which backend is configured (no secrets)."""
    backend = settings.gpu_backend.lower()
    configured = backend == "http" and bool(settings.gpu_endpoint)
    endpoint_host = ""
    if settings.gpu_endpoint:
        from urllib.parse import urlparse

        endpoint_host = urlparse(settings.gpu_endpoint).netloc or settings.gpu_endpoint
    return {
        "backend": backend,
        "configured": configured,
        "endpoint_host": endpoint_host,  # host only -- never the api key
    }
