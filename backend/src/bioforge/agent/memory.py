"""Memory retrieval for agent runs.

Called once at the start of `run_agent` to build a textual summary of relevant project
memory, which gets appended to the planner's user message. The planner uses this
context to make better-informed plans (e.g., "user prefers Homo sapiens GRCh38" → choose
the right BLAST database without asking).

Phase 1 retrieval strategy: return ALL memory entries for the project, capped at 20 most-
recent. This is dumb but correct for small memory stores (< ~50 entries). Smarter
retrieval (semantic, query-aware) lands when memory grows past where naive dumping is
useful.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bioforge.db.models import Project, ProjectMemory


async def load_relevant_memory(session: AsyncSession, project_id: str, goal: str, limit: int = 20) -> str:
    """Return a markdown-formatted memory summary, or an empty string if there's nothing
    to inject. Safe to call when the project has no memory entries — returns ''."""
    if not project_id or session is None:
        return ""

    project_row = await session.get(Project, project_id)

    stmt = (
        select(ProjectMemory)
        .where(ProjectMemory.project_id == project_id)
        .order_by(ProjectMemory.updated_at.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()

    if not rows and project_row is None:
        return ""

    lines: list[str] = ["# Project context"]

    if project_row is not None:
        bits = []
        if project_row.organism:
            bits.append(f"organism: {project_row.organism}")
        if project_row.reference_genome:
            bits.append(f"reference genome: {project_row.reference_genome}")
        if project_row.description:
            bits.append(project_row.description.strip())
        if bits:
            lines.append("- **project**: " + "; ".join(bits))

    if rows:
        lines.append("")
        lines.append("## Memory (most recent first)")
        for row in rows:
            entry = f"- **{row.key}** ({row.kind}, source={row.source}): {row.value}"
            if row.rationale:
                entry += f" _(why: {row.rationale})_"
            lines.append(entry)

    if len(lines) == 1:
        # Only the heading — no actual content.
        return ""

    lines.append("")
    lines.append(
        "Use this context to inform your plan. Do NOT treat it as authoritative for "
        "the current goal's biological claims — those still need tool calls."
    )
    return "\n".join(lines)
