"""Tests for the cloud-GPU execution path (Limitation #3).

No real GPU or network: the HTTP backend is driven by an injected fake client, so the
submit->poll->result loop, failure, timeout, and the honest 'not configured' refusal are all
covered hermetically. Plus the /gpu/status + /gpu/submit endpoints.
"""

from __future__ import annotations

from typing import Any

import pytest
from bioforge.config import settings
from bioforge.gpu.backend import (
    GpuBackendNotConfiguredError,
    GpuExecutionError,
    GpuJobResult,
    GpuJobSpec,
    HttpGpuBackend,
    NullGpuBackend,
    get_gpu_backend,
    gpu_capability,
)
from httpx import AsyncClient


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    """Records calls and returns scripted responses. Not a context manager (no __aenter__),
    so HttpGpuBackend uses it directly."""

    def __init__(self, *, submit: _FakeResponse, polls: list[_FakeResponse]) -> None:
        self._submit = submit
        self._polls = polls
        self._poll_i = 0
        self.posted: list[dict[str, Any]] = []
        self.get_urls: list[str] = []

    async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str]) -> _FakeResponse:
        self.posted.append({"url": url, "json": json, "headers": headers})
        return self._submit

    async def get(self, url: str, *, headers: dict[str, str]) -> _FakeResponse:
        self.get_urls.append(url)
        resp = self._polls[min(self._poll_i, len(self._polls) - 1)]
        self._poll_i += 1
        return resp


# --- NullGpuBackend / factory -----------------------------------------------------------


@pytest.mark.asyncio
async def test_null_backend_refuses_honestly() -> None:
    with pytest.raises(GpuBackendNotConfiguredError, match="No GPU backend is configured"):
        await NullGpuBackend().run(GpuJobSpec(task="anything"))


def test_factory_returns_null_when_none(monkeypatch) -> None:
    monkeypatch.setattr(settings, "gpu_backend", "none")
    assert isinstance(get_gpu_backend(), NullGpuBackend)


def test_factory_returns_http_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "gpu_backend", "http")
    monkeypatch.setattr(settings, "gpu_endpoint", "https://gpu.example.com")
    backend = get_gpu_backend()
    assert isinstance(backend, HttpGpuBackend)
    assert backend.endpoint == "https://gpu.example.com"


def test_factory_unknown_backend_raises(monkeypatch) -> None:
    monkeypatch.setattr(settings, "gpu_backend", "wat")
    with pytest.raises(GpuBackendNotConfiguredError, match="Unknown"):
        get_gpu_backend()


def test_http_backend_requires_endpoint() -> None:
    with pytest.raises(GpuBackendNotConfiguredError, match="BIOFORGE_GPU_ENDPOINT"):
        HttpGpuBackend(endpoint="")


# --- HttpGpuBackend submit -> poll -> result --------------------------------------------


@pytest.mark.asyncio
async def test_http_backend_completes() -> None:
    client = _FakeClient(
        submit=_FakeResponse({"job_id": "remote-123"}),
        polls=[
            _FakeResponse({"status": "running"}),
            _FakeResponse({"status": "completed", "result": {"pdb": "ATOM..."}}),
        ],
    )
    backend = HttpGpuBackend(
        endpoint="https://gpu.example.com/",
        api_key="secret",
        poll_seconds=0.0,
        client_factory=lambda: client,
    )
    result = await backend.run(GpuJobSpec(task="boltz_structure", inputs={"seq": "MKT"}))
    assert result.status == "completed"
    assert result.result == {"pdb": "ATOM..."}
    assert result.remote_job_id == "remote-123"
    # Submitted to the right URL with the bearer header + payload.
    assert client.posted[0]["url"] == "https://gpu.example.com/jobs"
    assert client.posted[0]["headers"]["Authorization"] == "Bearer secret"
    assert client.posted[0]["json"] == {"task": "boltz_structure", "inputs": {"seq": "MKT"}}


@pytest.mark.asyncio
async def test_http_backend_reports_failure() -> None:
    client = _FakeClient(
        submit=_FakeResponse({"job_id": "j1"}),
        polls=[_FakeResponse({"status": "failed", "error": "CUDA OOM"})],
    )
    backend = HttpGpuBackend(endpoint="https://g", poll_seconds=0.0, client_factory=lambda: client)
    result = await backend.run(GpuJobSpec(task="x"))
    assert result.status == "failed"
    assert result.error == "CUDA OOM"


@pytest.mark.asyncio
async def test_http_backend_times_out() -> None:
    client = _FakeClient(submit=_FakeResponse({"job_id": "j1"}), polls=[_FakeResponse({"status": "running"})])
    # Negative timeout -> the first poll-loop check trips immediately (deterministic).
    backend = HttpGpuBackend(
        endpoint="https://g", timeout_seconds=-1.0, poll_seconds=0.0, client_factory=lambda: client
    )
    with pytest.raises(GpuExecutionError, match="did not finish"):
        await backend.run(GpuJobSpec(task="x"))


@pytest.mark.asyncio
async def test_http_backend_missing_job_id_raises() -> None:
    client = _FakeClient(submit=_FakeResponse({}), polls=[])
    backend = HttpGpuBackend(endpoint="https://g", client_factory=lambda: client)
    with pytest.raises(GpuExecutionError, match="did not return a job_id"):
        await backend.run(GpuJobSpec(task="x"))


@pytest.mark.asyncio
async def test_http_backend_http_error_raises() -> None:
    client = _FakeClient(submit=_FakeResponse({"detail": "nope"}, status_code=500), polls=[])
    backend = HttpGpuBackend(endpoint="https://g", client_factory=lambda: client)
    with pytest.raises(GpuExecutionError, match="HTTP 500"):
        await backend.run(GpuJobSpec(task="x"))


# --- capability report ------------------------------------------------------------------


def test_capability_none(monkeypatch) -> None:
    monkeypatch.setattr(settings, "gpu_backend", "none")
    monkeypatch.setattr(settings, "gpu_endpoint", "")
    cap = gpu_capability()
    assert cap["backend"] == "none"
    assert cap["configured"] is False
    assert cap["endpoint_host"] == ""


def test_capability_http_hides_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "gpu_backend", "http")
    monkeypatch.setattr(settings, "gpu_endpoint", "https://gpu.example.com:8443/api")
    monkeypatch.setattr(settings, "gpu_api_key", "supersecret")
    cap = gpu_capability()
    assert cap["configured"] is True
    assert cap["endpoint_host"] == "gpu.example.com:8443"
    assert "supersecret" not in str(cap)  # never leak the key


# --- API endpoints ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_endpoint_default_not_configured(streaming_client: AsyncClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "gpu_backend", "none")
    resp = await streaming_client.get("/gpu/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["backend"] == "none"
    assert body["configured"] is False


@pytest.mark.asyncio
async def test_submit_503_when_not_configured(streaming_client: AsyncClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "gpu_backend", "none")
    resp = await streaming_client.post("/gpu/submit", json={"task": "boltz", "inputs": {}})
    assert resp.status_code == 503
    assert "No GPU backend is configured" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_submit_success_with_configured_backend(streaming_client: AsyncClient, monkeypatch) -> None:
    class _OkBackend:
        name = "http"

        async def run(self, spec: GpuJobSpec) -> GpuJobResult:
            return GpuJobResult(status="completed", result={"echo": spec.task}, remote_job_id="r1")

    monkeypatch.setattr("bioforge.api.gpu.get_gpu_backend", lambda: _OkBackend())
    resp = await streaming_client.post("/gpu/submit", json={"task": "demo", "inputs": {"a": 1}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "completed"
    assert body["result"] == {"echo": "demo"}
    assert body["remote_job_id"] == "r1"
