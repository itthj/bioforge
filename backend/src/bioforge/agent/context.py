"""Per-request agent context exposed to tools via `contextvars`.

The vast majority of bio tools take a typed Pydantic input and produce a typed output —
no need for ambient state. The memory tools (`recall_memory`, `remember`) are the
exception: they need the current project_id and a DB session to do their job. Threading
those through every tool's signature would be ceremony for the one-percent case.

So: ContextVars. The API layer wraps each run_agent / resume_agent call with an
`AgentContextScope`, which sets `project_id` and `session` for the duration. Tools that
need them call `get_current_project_id()` / `get_current_db_session()`. Tools that don't
ignore the context vars entirely.

ContextVars are asyncio-safe: each `asyncio.Task` inherits the current Context at
creation, so streaming + concurrent agent runs do not leak each other's state.
"""

from __future__ import annotations

import contextvars
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


_project_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "bioforge_project_id", default=None
)
_db_session_var: contextvars.ContextVar["AsyncSession | None"] = contextvars.ContextVar(
    "bioforge_db_session", default=None
)


def get_current_project_id() -> str | None:
    return _project_id_var.get()


def get_current_db_session() -> "AsyncSession | None":
    return _db_session_var.get()


class AgentContextScope:
    """Context manager that scopes project_id + session for one agent run.

    Use from the API layer:

        with AgentContextScope(project_id=body.project_id, session=session):
            result = await run_agent(body.goal, project_id=body.project_id, ...)

    Inside any tool handler invoked during that run, `get_current_project_id()` and
    `get_current_db_session()` return the scoped values.
    """

    def __init__(
        self, *, project_id: str | None, session: "AsyncSession | None"
    ) -> None:
        self._project_id = project_id
        self._session = session
        self._tokens: list[contextvars.Token] = []

    def __enter__(self) -> "AgentContextScope":
        self._tokens.append(_project_id_var.set(self._project_id))
        self._tokens.append(_db_session_var.set(self._session))
        return self

    def __exit__(self, *exc_info: object) -> None:
        for token in reversed(self._tokens):
            token.var.reset(token)
        self._tokens.clear()
