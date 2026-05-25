from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel


class ToolInput(BaseModel):
    """Base class for all tool input schemas."""

    model_config = {"extra": "forbid"}


class ToolOutput(BaseModel):
    """Base class for all tool output schemas.

    `tool_name`, `tool_version`, and `citations` are populated by the executor wrapper —
    tool handlers don't need to set them.
    """

    tool_name: str = ""
    tool_version: str = ""
    citations: list[str] = []

    model_config = {"extra": "allow"}


class ToolError(Exception):
    """Raised by a tool handler when it cannot produce a valid result.

    The message is returned to the model verbatim as a `tool_result` with `is_error: True`,
    so write it for the model to read — concrete and recoverable.
    """


CostHint = Literal["cheap", "moderate", "expensive"]


@dataclass
class ToolSpec:
    name: str
    description: str
    input_model: type[ToolInput]
    output_model: type[ToolOutput]
    handler: Callable[[ToolInput], Awaitable[ToolOutput]]
    version: str
    citations: list[str] = field(default_factory=list)
    cost_hint: CostHint = "cheap"
    destructive: bool = False
    tags: list[str] = field(default_factory=list)

    def anthropic_definition(self) -> dict:
        """Render as an Anthropic tool definition (name, description, input_schema)."""
        schema = self.input_model.model_json_schema()
        # Anthropic accepts JSON Schema directly; strip the title to keep things tidy.
        schema.pop("title", None)
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": schema,
        }
