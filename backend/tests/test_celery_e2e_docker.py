"""Docker-gated end-to-end test for the durable-job / Celery stack (Celery phase, slice 7).

Unlike the hermetic eager tests (test_celery_jobs.py), this brings up the REAL stack -- Postgres +
Redis + the API + a separate Celery worker PROCESS -- via docker compose, then drives a genuine
submit -> poll -> terminal round-trip over HTTP. It proves the cross-process durability claim that
eager mode (one process, one DB) cannot: the API enqueues, the worker (another container) executes
and persists to Postgres, and the API reads the finished run back.

Heavily gated -- it builds images, starts four containers, and makes a real LLM call:
  - `-m docker` (deselected by default), AND
  - BIOFORGE_CELERY_E2E=1 (explicit opt-in -- too expensive to run implicitly), AND
  - ANTHROPIC_API_KEY set (the worker runs a real agent loop).
Skips (never fails) when any is missing, so it is safe to leave in the suite.

Run it:
    BIOFORGE_CELERY_E2E=1 ANTHROPIC_API_KEY=sk-... \
        .venv/Scripts/python.exe -m pytest backend/tests/test_celery_e2e_docker.py -m docker -q
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from bioforge.config import settings

pytestmark = pytest.mark.docker

# Repo root holds docker-compose.yml: tests -> backend -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = [
    "docker",
    "compose",
    "-f",
    "docker-compose.yml",
    "-f",
    "docker-compose.e2e.yml",
    "--profile",
    "workers",
]
_SERVICES = ["postgres", "redis", "backend", "worker"]
_BASE_URL = "http://localhost:18000"


def _skip_unless_ready() -> None:
    if shutil.which("docker") is None:
        pytest.skip("docker not on PATH")
    if os.environ.get("BIOFORGE_CELERY_E2E") != "1":
        pytest.skip("opt-in only: set BIOFORGE_CELERY_E2E=1 to run the full compose round-trip")
    if not settings.anthropic_api_key:
        pytest.skip("needs ANTHROPIC_API_KEY -- the worker runs a real agent loop")


def _wait_for(url: str, *, timeout: float, want: tuple[int, ...] = (200,)) -> httpx.Response:
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=5)
            if resp.status_code in want:
                return resp
        except httpx.HTTPError as e:  # container still starting
            last_exc = e
        time.sleep(2)
    raise AssertionError(f"{url} not ready within {timeout}s (last error: {last_exc})")


def _poll_terminal(url: str, *, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        resp = httpx.get(url, timeout=10)
        assert resp.status_code == 200, resp.text
        last = resp.json()
        if last["status"] not in ("queued", "running"):
            return last
        time.sleep(2)
    raise AssertionError(f"run {url} never reached a terminal state within {timeout}s (last: {last})")


def test_celery_durable_job_end_to_end() -> None:
    """Submit a cheap run to the real API in celery mode, then confirm the worker (a separate
    container) ran it and persisted a terminal trace to the shared Postgres that the API reads back."""
    _skip_unless_ready()

    up = subprocess.run(
        [*_COMPOSE, "up", "-d", "--build", *_SERVICES],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert up.returncode == 0, f"compose up failed:\n{up.stderr}"
    try:
        _wait_for(f"{_BASE_URL}/health", timeout=180)

        # Enqueue: celery mode returns immediately with a queued trace_id (no in-request execution).
        submit = httpx.post(
            f"{_BASE_URL}/agent/run",
            json={"goal": "Compute the GC content of ATGCATGCATGCATGC and report the percentage."},
            timeout=30,
        )
        assert submit.status_code == 200, submit.text
        body = submit.json()
        assert body["status"] == "queued", body
        trace_id = body["trace_id"]

        # The worker (separate process) executes + persists to Postgres; the API reads it back.
        final = _poll_terminal(f"{_BASE_URL}/traces/{trace_id}", timeout=240)
        assert final["status"] in ("completed", "completed_after_replan", "refused", "iteration_cap"), final
        assert final["steps"], "the worker persisted no steps"
    finally:
        subprocess.run(
            [*_COMPOSE, "down", "-v"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
