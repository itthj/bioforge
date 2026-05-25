"""Memory tools — the agent's only way to read or write project memory.

These tools reach the per-request DB session and project_id via the ContextVars in
`agent/context.py`. The API layer sets that scope before kicking off `run_agent`; if
either ContextVar is empty (e.g. the tool was invoked from a non-request code path) the
tool raises ToolError rather than silently doing nothing.

`remember` writes are upserts by `(project_id, key)`. The user can later inspect or edit
any entry via `PATCH /projects/{id}/memory/{key}`. Provenance is recorded in
`ProjectMemory.source`: "agent" for these tools, "user" for direct edits.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select

from bioforge.agent.context import get_current_db_session, get_current_project_id
from bioforge.db.models import ProjectMemory
from bioforge.tools.base import ToolError, ToolInput, ToolOutput
from bioforge.tools.registry import register_tool

# --- recall_memory --------------------------------------------------------------------


class RecallMemoryInput(ToolInput):
    query: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "Substring to search for, case-insensitive, against both memory keys and "
            "values. Returns the most recently updated matches first, capped at 20."
        ),
    )


class MemoryHit(BaseModel):
    key: str
    value: str
    kind: str
    source: str
    rationale: str | None
    updated_at: str


class RecallMemoryOutput(ToolOutput):
    matches: list[MemoryHit]
    count: int


@register_tool(
    name="recall_memory",
    description=(
        "Look up facts in this project's memory. Use when the user references something "
        "you might have learned in a past session — preferred organism, naming "
        "conventions, prior result summaries, file references. Returns memory entries "
        "whose key or value contains the substring. Memory is scoped to the current "
        "project. Returns an empty list when no entries match."
    ),
    input_model=RecallMemoryInput,
    output_model=RecallMemoryOutput,
    version="1.0.0",
    citations=["BioForge project memory store"],
    cost_hint="cheap",
    destructive=False,
    tags=["memory"],
)
async def recall_memory(inp: RecallMemoryInput) -> RecallMemoryOutput:
    project_id = get_current_project_id()
    session = get_current_db_session()
    if not project_id or session is None:
        raise ToolError(
            "recall_memory has no project context. This tool is only callable inside an "
            "agent run; the API layer must set AgentContextScope before run_agent."
        )

    pattern = f"%{inp.query.lower()}%"
    stmt = (
        select(ProjectMemory)
        .where(
            ProjectMemory.project_id == project_id,
            or_(
                func.lower(ProjectMemory.key).like(pattern),
                func.lower(ProjectMemory.value).like(pattern),
            ),
        )
        .order_by(ProjectMemory.updated_at.desc())
        .limit(20)
    )
    rows = (await session.execute(stmt)).scalars().all()
    matches = [
        MemoryHit(
            key=row.key,
            value=row.value,
            kind=row.kind,
            source=row.source,
            rationale=row.rationale,
            updated_at=row.updated_at.isoformat(),
        )
        for row in rows
    ]
    return RecallMemoryOutput(matches=matches, count=len(matches))


# --- remember -------------------------------------------------------------------------


class RememberInput(ToolInput):
    key: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[a-zA-Z0-9_-]+$",
        description=(
            "Short stable identifier for this memory entry. Writes with the same key "
            "UPDATE the existing entry rather than creating a duplicate. Use snake_case "
            "or kebab-case. Examples: 'preferred_organism', 'naming-convention'."
        ),
    )
    value: str = Field(
        min_length=1,
        max_length=4000,
        description="The fact, preference, or summary to remember. Plain text.",
    )
    kind: Literal["fact", "preference", "summary", "file_reference"] = Field(
        description=(
            "Category. 'fact' = stable truth about the project. 'preference' = how the "
            "user likes things done. 'summary' = compressed past-analysis result. "
            "'file_reference' = pointer to a sequence/dataset stored elsewhere."
        )
    )
    rationale: str | None = Field(
        default=None,
        max_length=500,
        description=(
            "Why this is worth remembering (one sentence). Helps the user audit memory."
        ),
    )

    @field_validator("value")
    @classmethod
    def _no_runaway_remembering(cls, v: str) -> str:
        # The agent should remember durable preferences, not long-winded paste-dumps.
        # A 4000-char cap is generous but defends against accidentally writing entire
        # tool outputs into memory.
        return v.strip()


class RememberOutput(ToolOutput):
    key: str
    operation: Literal["created", "updated"]


@register_tool(
    name="remember",
    description=(
        "Save a durable fact or preference to this project's memory. Call ONLY when the "
        "user states a lasting preference (e.g., 'I always work with Homo sapiens "
        "GRCh38') or you learn something that will matter in future sessions. Do NOT "
        "remember transient details, results of the current analysis (those are in the "
        "trace), or anything the user hasn't endorsed. The user can audit and edit "
        "memory via the /projects/{id}/memory API."
    ),
    input_model=RememberInput,
    output_model=RememberOutput,
    version="1.0.0",
    citations=["BioForge project memory store"],
    cost_hint="cheap",
    destructive=False,
    tags=["memory"],
)
async def remember(inp: RememberInput) -> RememberOutput:
    project_id = get_current_project_id()
    session = get_current_db_session()
    if not project_id or session is None:
        raise ToolError(
            "remember has no project context. This tool is only callable inside an "
            "agent run; the API layer must set AgentContextScope before run_agent."
        )

    existing = (
        await session.execute(
            select(ProjectMemory).where(
                ProjectMemory.project_id == project_id, ProjectMemory.key == inp.key
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.value = inp.value
        existing.kind = inp.kind
        existing.rationale = inp.rationale
        existing.source = "agent"
        existing.updated_at = datetime.now(UTC)
        operation: Literal["created", "updated"] = "updated"
    else:
        new = ProjectMemory(
            project_id=project_id,
            key=inp.key,
            value=inp.value,
            kind=inp.kind,
            rationale=inp.rationale,
            source="agent",
        )
        session.add(new)
        operation = "created"

    await session.flush()
    return RememberOutput(key=inp.key, operation=operation)
