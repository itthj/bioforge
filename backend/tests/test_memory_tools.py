"""Direct tests for recall_memory / remember.

These tools rely on ContextVars (project_id + db_session) being set by the API layer.
We set the scope manually here via `AgentContextScope` and call the registered tool
handlers directly, so the tests don't depend on Anthropic or the agent loop.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from bioforge.agent.context import AgentContextScope
from bioforge.db.models import Project, ProjectMemory
from bioforge.tools.base import ToolError
from bioforge.tools.meta.memory_tools import (
    RecallMemoryInput,
    RememberInput,
    recall_memory,
    remember,
)


@pytest_asyncio.fixture
async def memory_scope(test_session_maker):
    """Yields (session, project_id) inside an AgentContextScope. Tools invoked during
    the test see the scoped project + session via ContextVars."""
    async with test_session_maker() as session:
        session.add(Project(id="mem-test", name="Memory test"))
        await session.commit()
        with AgentContextScope(project_id="mem-test", session=session):
            yield session


async def test_remember_creates_new_entry(memory_scope) -> None:
    out = await remember(
        RememberInput(
            key="preferred_organism",
            value="Homo sapiens",
            kind="preference",
            rationale="User stated this is their default working species.",
        )
    )
    assert out.key == "preferred_organism"
    assert out.operation == "created"
    # tool_name/version are stamped by execute_tool(), not the handler itself —
    # provenance is covered by test_registry.test_execute_tool_validates_input_and_stamps_provenance.

    # Verify it actually landed in the DB.
    from sqlalchemy import select

    row = (
        await memory_scope.execute(select(ProjectMemory).where(ProjectMemory.key == "preferred_organism"))
    ).scalar_one()
    assert row.value == "Homo sapiens"
    assert row.source == "agent"


async def test_remember_updates_existing_entry(memory_scope) -> None:
    await remember(RememberInput(key="ref", value="GRCh38", kind="preference"))
    out = await remember(RememberInput(key="ref", value="GRCh37", kind="preference"))
    assert out.operation == "updated"

    from sqlalchemy import select

    rows = (await memory_scope.execute(select(ProjectMemory).where(ProjectMemory.key == "ref"))).scalars().all()
    assert len(rows) == 1  # update, not duplicate
    assert rows[0].value == "GRCh37"


async def test_recall_memory_substring_match_on_key_and_value(memory_scope) -> None:
    await remember(RememberInput(key="preferred_organism", value="Homo sapiens", kind="preference"))
    await remember(RememberInput(key="naming_convention", value="snake_case for genes", kind="preference"))
    await remember(RememberInput(key="ref_genome", value="GRCh38", kind="preference"))

    # Match in the key
    out = await recall_memory(RecallMemoryInput(query="organism"))
    assert out.count == 1
    assert out.matches[0].key == "preferred_organism"

    # Match in the value
    out = await recall_memory(RecallMemoryInput(query="snake"))
    assert out.count == 1
    assert out.matches[0].key == "naming_convention"

    # No match
    out = await recall_memory(RecallMemoryInput(query="zebrafish"))
    assert out.count == 0
    assert out.matches == []


async def test_recall_memory_returns_most_recent_first(memory_scope) -> None:
    import asyncio

    await remember(RememberInput(key="first_key", value="thing one", kind="fact"))
    await asyncio.sleep(0.01)
    await remember(RememberInput(key="second_key", value="thing two", kind="fact"))

    out = await recall_memory(RecallMemoryInput(query="thing"))
    assert out.count == 2
    assert out.matches[0].key == "second_key"  # newest first
    assert out.matches[1].key == "first_key"


async def test_memory_tools_refuse_without_scope() -> None:
    """Tools must not silently no-op when run outside AgentContextScope."""
    with pytest.raises(ToolError, match="no project context"):
        await remember(RememberInput(key="k", value="v", kind="fact"))
    with pytest.raises(ToolError, match="no project context"):
        await recall_memory(RecallMemoryInput(query="anything"))


async def test_remember_rejects_invalid_key() -> None:
    """Keys must be `[a-zA-Z0-9_-]+`. No spaces, no dots, no slashes."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        RememberInput(key="has spaces", value="x", kind="fact")
    with pytest.raises(pydantic.ValidationError):
        RememberInput(key="bad/slash", value="x", kind="fact")


async def test_memory_isolation_between_projects(test_session_maker) -> None:
    """A remember in project A must not be visible from project B."""
    async with test_session_maker() as session:
        session.add(Project(id="proj-a", name="A"))
        session.add(Project(id="proj-b", name="B"))
        await session.commit()

        with AgentContextScope(project_id="proj-a", session=session):
            await remember(RememberInput(key="a_secret", value="only in A", kind="fact"))
        with AgentContextScope(project_id="proj-b", session=session):
            out = await recall_memory(RecallMemoryInput(query="secret"))
            assert out.count == 0  # B cannot see A's memory
