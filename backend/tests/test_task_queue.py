"""Tests for the TaskQueue abstraction.

The Inline path is the only one that runs end-to-end in CI (Redis is an
optional service). The Celery path is verified at the contract level —
it satisfies the Protocol and the env-based selection picks it up — but
its `submit` / `result` exercise is left to integration tests that bring
up the worker container.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from bioforge.tasks import (
    CeleryTaskQueue,
    InlineTaskQueue,
    TaskQueue,
    TaskStatus,
    get_task_queue,
)
from bioforge.tasks.queue import reset_task_queue


@pytest.fixture(autouse=True)
def _reset_queue_singleton():
    reset_task_queue()
    yield
    reset_task_queue()


# --- Protocol conformance -----------------------------------------------------


def test_inline_queue_satisfies_protocol() -> None:
    assert isinstance(InlineTaskQueue(), TaskQueue)


def test_celery_queue_satisfies_protocol() -> None:
    """We construct CeleryTaskQueue with a stub app so we don't need Redis."""

    class _StubApp:
        def send_task(self, name, args=None, kwargs=None):  # noqa: ANN001
            return type("R", (), {"id": "fake-id"})()

    q = CeleryTaskQueue(app=_StubApp())
    assert isinstance(q, TaskQueue)


# --- Env-driven selection -----------------------------------------------------


def test_default_selection_is_inline(monkeypatch) -> None:
    monkeypatch.delenv("BIOFORGE_TASK_QUEUE", raising=False)
    reset_task_queue()
    q = get_task_queue()
    assert isinstance(q, InlineTaskQueue)
    assert q.backend_name == "inline"


def test_celery_selection_when_env_set(monkeypatch) -> None:
    monkeypatch.setenv("BIOFORGE_TASK_QUEUE", "celery")
    reset_task_queue()
    # Construction of CeleryTaskQueue lazily imports celery_app; we patch the
    # import so the test doesn't need a real broker.
    fake_app = type("App", (), {"send_task": lambda self, *a, **kw: type("R", (), {"id": "x"})()})()
    with patch("bioforge.tasks.celery_app.celery_app", fake_app):
        q = get_task_queue()
    assert isinstance(q, CeleryTaskQueue)
    assert q.backend_name == "celery"


def test_unknown_value_falls_back_to_inline(monkeypatch) -> None:
    monkeypatch.setenv("BIOFORGE_TASK_QUEUE", "kubernetes-someday")
    reset_task_queue()
    q = get_task_queue()
    assert isinstance(q, InlineTaskQueue)


# --- Inline end-to-end --------------------------------------------------------


async def test_inline_submit_runs_tool_immediately() -> None:
    """Submitting a known cheap tool via Inline returns a populated handle."""
    q = InlineTaskQueue()
    handle = await q.submit("gc_content", {"sequence": "ATGCATGC"})
    assert handle.status == TaskStatus.success
    assert handle.backend == "inline"
    assert handle.result is not None
    # gc_content output schema includes 'gc_percent'.
    assert "gc_percent" in handle.result


async def test_inline_result_is_idempotent() -> None:
    """Calling .result on an inline handle just returns the handle."""
    q = InlineTaskQueue()
    h1 = await q.submit("gc_content", {"sequence": "ATGCATGC"})
    h2 = await q.result(h1)
    assert h2.task_id == h1.task_id
    assert h2.status == h1.status
    assert h2.result == h1.result


async def test_inline_failure_captured_as_failure_status() -> None:
    """Tool errors propagate as TaskStatus.failure with the error text."""
    q = InlineTaskQueue()
    # Invalid input — sequence min_length=1 in gc_content.
    handle = await q.submit("gc_content", {"sequence": ""})
    assert handle.status == TaskStatus.failure
    assert handle.error is not None


async def test_inline_unknown_tool_is_failure() -> None:
    q = InlineTaskQueue()
    handle = await q.submit("does_not_exist", {"foo": "bar"})
    assert handle.status == TaskStatus.failure
    # The error message points at the missing tool.
    assert "does_not_exist" in (handle.error or "") or "Tool" in (handle.error or "")
