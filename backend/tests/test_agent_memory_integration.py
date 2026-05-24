"""Agent + memory integration.

The key assertion: memory written by the `remember` tool in one run IS visible to the
planner on the next run (because `load_relevant_memory` injects it into the planner's
user message). This is the full round-trip the project-memory spec promises.

These tests use run_agent directly with a FakeLLM and `AgentContextScope` to set the
ContextVars — no FastAPI. The HTTP plumbing is already validated by test_projects.py.
"""

from __future__ import annotations

import pytest_asyncio

from bioforge.agent import run_agent
from bioforge.agent.context import AgentContextScope
from bioforge.db.models import Project, ProjectMemory


@pytest_asyncio.fixture
async def mem_session(test_session_maker):
    """A session inside an AgentContextScope for project `mem-int`."""
    async with test_session_maker() as session:
        session.add(
            Project(
                id="mem-int",
                name="Memory integration test",
                organism="Homo sapiens",
                reference_genome="GRCh38",
            )
        )
        await session.commit()
        with AgentContextScope(project_id="mem-int", session=session):
            yield session


async def test_agent_remember_then_recall_across_runs(
    mem_session,
    fake_llm_factory,
    make_submit_plan_response,
    make_tool_use_response,
    make_text_response,
    trivial_plan,
) -> None:
    """Run 1: agent calls `remember` to save a preference. Run 2: same goal, agent calls
    `recall_memory`. The remember from run 1 must be visible to recall in run 2 (different
    LLM scripts, same project/session)."""

    # --- Run 1: remember preferred_organism ---
    llm1 = fake_llm_factory(
        [
            make_submit_plan_response(trivial_plan(tool_name="remember")),
            make_tool_use_response(
                "remember",
                {
                    "key": "preferred_organism",
                    "value": "Homo sapiens (GRCh38)",
                    "kind": "preference",
                    "rationale": "User stated this preference in chat.",
                },
            ),
            make_text_response("Saved preferred organism: Homo sapiens (GRCh38)."),
        ]
    )
    result1 = await run_agent(
        "Remember that I always work with Homo sapiens GRCh38.",
        project_id="mem-int",
        llm=llm1,
    )
    assert result1.status == "completed"
    tool_steps = [s for s in result1.steps if s.type == "tool_call"]
    assert any(s.tool_name == "remember" for s in tool_steps)

    # Verify the memory landed in the DB.
    from sqlalchemy import select

    row = (
        await mem_session.execute(
            select(ProjectMemory).where(ProjectMemory.key == "preferred_organism")
        )
    ).scalar_one()
    assert "Homo sapiens" in row.value

    # --- Run 2: recall_memory finds it ---
    llm2 = fake_llm_factory(
        [
            make_submit_plan_response(trivial_plan(tool_name="recall_memory")),
            make_tool_use_response(
                "recall_memory", {"query": "organism"}
            ),
            make_text_response("Your preferred organism is Homo sapiens (GRCh38)."),
        ]
    )
    result2 = await run_agent(
        "What's my preferred organism?",
        project_id="mem-int",
        llm=llm2,
    )
    assert result2.status == "completed"
    tool_steps2 = [s for s in result2.steps if s.type == "tool_call"]
    assert tool_steps2[0].tool_name == "recall_memory"
    assert tool_steps2[0].tool_output["count"] == 1
    assert "Homo sapiens" in tool_steps2[0].tool_output["matches"][0]["value"]


async def test_planner_sees_project_context_and_memory(
    mem_session, fake_llm_factory, make_submit_plan_response, trivial_plan
) -> None:
    """`load_relevant_memory` should inject the project's organism, reference genome,
    AND any persisted memory into the planner's user message."""
    # Seed a memory entry.
    mem_session.add(
        ProjectMemory(
            project_id="mem-int",
            key="naming_convention",
            value="snake_case for genes",
            kind="preference",
            source="user",
        )
    )
    await mem_session.commit()

    llm = fake_llm_factory(
        [make_submit_plan_response(trivial_plan(tool_name="gc_content"))]
    )
    await run_agent(
        "GC content of ATGCATGC",
        project_id="mem-int",
        llm=llm,
        skip_approval_gate=True,
    )

    # The planner's user message (calls[0].messages[0]['content']) should mention the
    # project's organism, ref genome, and seeded memory entry.
    planner_user_content = llm.calls[0].messages[0]["content"]
    assert "Homo sapiens" in planner_user_content
    assert "GRCh38" in planner_user_content
    assert "snake_case" in planner_user_content
    assert "naming_convention" in planner_user_content


async def test_no_memory_context_when_project_has_no_data(
    test_session_maker,
    fake_llm_factory,
    make_submit_plan_response,
    trivial_plan,
) -> None:
    """An empty project + no memory entries should NOT inject any project-context block
    into the planner's user message. (Avoids confusing the planner with empty headings.)"""
    async with test_session_maker() as session:
        session.add(Project(id="empty-proj", name="empty"))
        await session.commit()

        with AgentContextScope(project_id="empty-proj", session=session):
            llm = fake_llm_factory(
                [make_submit_plan_response(trivial_plan(tool_name="gc_content"))]
            )
            await run_agent(
                "GC of ATGCATGC",
                project_id="empty-proj",
                llm=llm,
            )

    planner_user_content = llm.calls[0].messages[0]["content"]
    assert "# Project context" not in planner_user_content
