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
    # --- v4 §4.2 grounding / uncertainty / OOD metadata ---
    # Optional; defaults keep every existing tool registering unchanged. The dict-keyed
    # fields let a tool with several scores (e.g. on_target vs off_target) declare per-score
    # behavior. `published_accuracy` values must be sourced or carry a `VERIFY:` marker —
    # never an unsourced number.
    model_versions: dict[str, str] = field(default_factory=dict)
    emits_instance_uncertainty: dict[str, bool] = field(default_factory=dict)
    published_accuracy: dict[str, str] = field(default_factory=dict)
    training_distribution: dict[str, object] = field(default_factory=dict)
    reference_data_keys: list[str] = field(default_factory=list)

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


def uncertainty_note(spec: ToolSpec, key: str) -> str:
    """The §6 honesty rule, as code: report only the uncertainty a model actually provides.

    - emits per-instance uncertainty → say so (the value is in the tool's own output);
    - else if a model-level published accuracy is recorded → report that (it must be sourced);
    - else → an explicit "point estimate, no interval" framing.

    It NEVER fabricates a per-prediction interval or an accuracy figure — that is the entire
    point. `key` selects which declared score to describe (e.g. "on_target").
    """
    if spec.emits_instance_uncertainty.get(key):
        return f"{key}: instance-level uncertainty is provided in this tool's output — use it directly."
    accuracy = spec.published_accuracy.get(key)
    if accuracy:
        return f"{key}: point estimate only (no per-prediction interval). Model-level published accuracy: {accuracy}"
    return (
        f"{key}: point estimate only — no per-prediction interval and no published accuracy "
        "recorded; treat as qualitative."
    )
