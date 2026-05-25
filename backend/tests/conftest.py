"""Shared test fixtures: FakeLLM and helpers for constructing Anthropic Message objects.

We build real `anthropic.types.Message` instances rather than mocks so the type contracts
the agent loop reads (`.content[i].type`, `.stop_reason`, `.usage.*`) are exercised end to
end.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _build_text_response(
    text: str,
    *,
    model: str = "claude-sonnet-4-6",
    input_tokens: int = 100,
    output_tokens: int = 50,
    stop_reason: str = "end_turn",
) -> Message:
    return Message(
        id="msg_text_test",
        type="message",
        role="assistant",
        model=model,
        content=[TextBlock(type="text", text=text, citations=None)],
        stop_reason=stop_reason,
        stop_sequence=None,
        usage=Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            service_tier=None,
        ),
    )


def _build_submit_plan_response(
    plan: dict[str, Any],
    *,
    tool_use_id: str = "toolu_plan",
    model: str = "claude-sonnet-4-6",
    input_tokens: int = 200,
    output_tokens: int = 80,
) -> Message:
    return Message(
        id="msg_plan_test",
        type="message",
        role="assistant",
        model=model,
        content=[
            ToolUseBlock(type="tool_use", id=tool_use_id, name="submit_plan", input=plan)
        ],
        stop_reason="tool_use",
        stop_sequence=None,
        usage=Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            service_tier=None,
        ),
    )


def _build_submit_verdict_response(
    verdict: dict[str, Any],
    *,
    tool_use_id: str = "toolu_verdict",
    model: str = "claude-sonnet-4-6",
    input_tokens: int = 250,
    output_tokens: int = 60,
) -> Message:
    return Message(
        id="msg_verdict_test",
        type="message",
        role="assistant",
        model=model,
        content=[
            ToolUseBlock(
                type="tool_use", id=tool_use_id, name="submit_verdict", input=verdict
            )
        ],
        stop_reason="tool_use",
        stop_sequence=None,
        usage=Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            service_tier=None,
        ),
    )


def _build_tool_use_response(
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    tool_use_id: str = "toolu_test",
    preamble_text: str | None = None,
    model: str = "claude-sonnet-4-6",
    input_tokens: int = 100,
    output_tokens: int = 30,
) -> Message:
    content: list = []
    if preamble_text:
        content.append(TextBlock(type="text", text=preamble_text, citations=None))
    content.append(
        ToolUseBlock(type="tool_use", id=tool_use_id, name=tool_name, input=tool_input)
    )
    return Message(
        id="msg_tooluse_test",
        type="message",
        role="assistant",
        model=model,
        content=content,
        stop_reason="tool_use",
        stop_sequence=None,
        usage=Usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            service_tier=None,
        ),
    )


@dataclass
class CapturedCall:
    model: str
    system: Any
    messages: list[dict]
    tools: list[dict] | None
    tool_choice: dict | None
    max_tokens: int


class FakeLLM:
    """A scripted LLM stand-in. Returns `responses[i]` on the i-th `complete()` call.

    Captures each call so tests can assert what was sent (system prompt shape, tools
    passed, message turn structure).
    """

    def __init__(self, responses: Iterable[Message]) -> None:
        self._responses: list[Message] = list(responses)
        self._idx = 0
        self.calls: list[CapturedCall] = []

    async def complete(
        self,
        *,
        model: str,
        system: Any,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
        max_tokens: int = 4096,
    ) -> Message:
        # Mirror the real LLM.complete instrumentation so OTel-based assertions in tests
        # see `llm.complete` spans regardless of which LLM is injected.
        from opentelemetry import trace as _otel_trace

        _tracer = _otel_trace.get_tracer("bioforge.agent")
        with _tracer.start_as_current_span("llm.complete") as span:
            span.set_attribute("gen_ai.system", "anthropic")
            span.set_attribute("gen_ai.request.model", model)
            span.set_attribute("gen_ai.request.max_tokens", max_tokens)
            if tools:
                span.set_attribute("bioforge.tool_choice_count", len(tools))
            if tool_choice:
                span.set_attribute(
                    "bioforge.tool_choice_type", tool_choice.get("type", "")
                )

            self.calls.append(
                CapturedCall(
                    model=model,
                    system=system,
                    messages=[dict(m) for m in messages],
                    tools=list(tools) if tools else None,
                    tool_choice=dict(tool_choice) if tool_choice else None,
                    max_tokens=max_tokens,
                )
            )
            if self._idx >= len(self._responses):
                raise RuntimeError(
                    f"FakeLLM ran out of scripted responses (call #{self._idx + 1}). "
                    "Add more to the `responses=` list in the test."
                )
            resp = self._responses[self._idx]
            self._idx += 1

            span.set_attribute("gen_ai.response.model", model)
            span.set_attribute("gen_ai.usage.input_tokens", resp.usage.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", resp.usage.output_tokens)
            if resp.stop_reason:
                span.set_attribute("gen_ai.response.finish_reason", resp.stop_reason)
            return resp


@pytest.fixture
def make_text_response():
    """Factory fixture: build an Anthropic Message with one TextBlock and end_turn."""
    return _build_text_response


@pytest.fixture
def make_tool_use_response():
    """Factory fixture: build an Anthropic Message with a ToolUseBlock and tool_use stop."""
    return _build_tool_use_response


@pytest.fixture
def make_submit_plan_response():
    """Factory fixture: build a planner response — a tool_use call to `submit_plan`."""
    return _build_submit_plan_response


@pytest.fixture
def make_submit_verdict_response():
    """Factory fixture: build a critic response — a tool_use call to `submit_verdict`."""
    return _build_submit_verdict_response


def _trivial_plan(
    summary: str = "Single tool call.", tool_name: str | None = None
) -> dict:
    return {
        "is_trivial": True,
        "summary": summary,
        "steps": (
            [
                {
                    "idx": 0,
                    "description": "Run the only required tool.",
                    "expected_tool": tool_name,
                    "rationale": "Direct answer to the goal.",
                }
            ]
            if tool_name
            else []
        ),
    }


def _multi_step_plan(
    steps: list[tuple[str, str]], summary: str = "Multi-step approach."
) -> dict:
    return {
        "is_trivial": False,
        "summary": summary,
        "steps": [
            {
                "idx": i,
                "description": desc,
                "expected_tool": tool,
                "rationale": f"Step {i} feeds into step {i + 1}.",
            }
            for i, (tool, desc) in enumerate(steps)
        ],
    }


def _passing_verdict(
    reason: str = "Response covers the goal with grounded tool outputs.",
) -> dict:
    return {"satisfies_goal": True, "reason": reason, "concrete_complaints": []}


def _failing_verdict(
    complaints: list[str], reason: str = "Goal incompletely addressed."
) -> dict:
    return {
        "satisfies_goal": False,
        "reason": reason,
        "concrete_complaints": complaints,
    }


@pytest.fixture
def trivial_plan():
    """Factory: `trivial_plan(summary=..., tool_name=...)` → dict matching the Plan schema."""
    return _trivial_plan


@pytest.fixture
def multi_step_plan():
    """Factory: `multi_step_plan([("rev_comp","..."), ("gc_content","...")])` → dict."""
    return _multi_step_plan


@pytest.fixture
def passing_verdict():
    return _passing_verdict


@pytest.fixture
def failing_verdict():
    return _failing_verdict


@pytest.fixture
def fake_llm_factory():
    """Returns `factory(responses=[...]) -> FakeLLM`. One LLM per test."""

    def _factory(responses: Iterable[Message]) -> FakeLLM:
        return FakeLLM(responses)

    return _factory


# --- Per-test FastAPI + in-memory DB fixtures ---------------------------------------
#
# Shared by test_streaming.py and test_projects.py. The session factory uses a tmp_path-
# backed SQLite so two requests in the same test can see each other's writes (in-memory
# `:memory:` would give each connection its own DB).


import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402


@pytest_asyncio.fixture
async def test_session_maker(tmp_path):
    from bioforge.db.engine import Base

    db_url = f"sqlite+aiosqlite:///{tmp_path.as_posix()}/test.db"
    engine = create_async_engine(db_url, echo=False)
    from bioforge.db import models  # noqa: F401  — registers tables on Base.metadata

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def streaming_client(test_session_maker):
    """An httpx.AsyncClient bound to the FastAPI app, with get_session overridden to use
    the per-test on-disk SQLite. The default project row is also bootstrapped so
    /agent/run calls with project_id='default-project' don't fail FK constraints."""
    from bioforge.constants import DEFAULT_PROJECT_ID
    from bioforge.db.engine import get_session
    from bioforge.db.models import Project
    from bioforge.main import app
    from httpx import ASGITransport, AsyncClient

    async def override_get_session():
        async with test_session_maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = override_get_session

    # Bootstrap default project for tests that don't create their own.
    async with test_session_maker() as session:
        session.add(
            Project(id=DEFAULT_PROJECT_ID, name="Default project (test)")
        )
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def lambda_phage_fixture() -> dict[str, Any]:
    """Load the lambda phage fixture + its committed metadata.

    Skips the test if the fixture isn't generated yet — instructions in the skip message.
    """
    fasta_path = FIXTURE_DIR / "lambda_phage_1kb.fasta"
    meta_path = FIXTURE_DIR / "lambda_phage_1kb.meta.json"
    if not fasta_path.exists() or not meta_path.exists():
        pytest.skip(
            "Lambda phage fixture not generated. Run "
            "`python backend/tests/fixtures/regenerate.py` "
            "(requires BIOFORGE_ENTREZ_EMAIL in .env and network access to NCBI)."
        )
    sequence_lines = []
    for line in fasta_path.read_text().splitlines():
        if line.startswith(">") or not line.strip():
            continue
        sequence_lines.append(line.strip())
    sequence = "".join(sequence_lines)
    meta = json.loads(meta_path.read_text())
    return {"sequence": sequence, **meta}
