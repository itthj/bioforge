"""Layer 4 — entity & mechanistic grounding judge (BioForge v4 §4).

An LLM (Opus recommended) classifies and judges the non-numeric claims in a draft
response, constrained to support a claim only by citing a field that actually exists in
the run's structured tool outputs — never its own knowledge. This is the lossy, *measured*
layer (never trusted blindly): it exists to catch the "correct value, wrong
interpretation" and "mechanism with no backing field" failures the deterministic numeric
layer cannot see.

Uses forced tool-use (`submit_grounding`) — the same structured-output pattern as the
planner and critic, so there is no free-text path for the judge to wander off into.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from bioforge.agent.grounding.report import JudgedClaim
from bioforge.agent.llm import LLM, UsageSummary, summarize_usage

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


class GroundingJudgement(BaseModel):
    """The judge's structured output: one entry per entity/mechanistic claim found."""

    claims: list[JudgedClaim] = Field(default_factory=list)


SUBMIT_GROUNDING_TOOL: dict = {
    "name": "submit_grounding",
    "description": (
        "Submit your grounding judgement: one entry per entity or mechanistic claim in the "
        "draft response. This is how you respond — there is no free-text output."
    ),
    "input_schema": GroundingJudgement.model_json_schema(),
}


@dataclass
class JudgeResult:
    claims: list[JudgedClaim]
    usage: UsageSummary


def _load_judge_prompt() -> str:
    return (_PROMPTS_DIR / "grounding_judge.md").read_text(encoding="utf-8")


def _build_messages(response_text: str, tool_outputs: list[dict]) -> list[dict]:
    outputs_block = json.dumps(tool_outputs, indent=2, default=str)
    content = (
        "# Structured tool outputs (the ONLY valid source of support)\n\n"
        f"```json\n{outputs_block}\n```\n\n"
        f"# Draft response\n\n{response_text}\n\n"
        "Identify and judge every entity and mechanistic claim. Emit via `submit_grounding`."
    )
    return [{"role": "user", "content": content}]


async def judge_claims(
    *,
    response_text: str,
    tool_outputs: list[dict],
    llm: LLM,
    model: str,
) -> JudgeResult:
    """Run the L4 judge over a draft response. Returns the judged claims + token usage.

    Raises ValueError if the model does not return a valid `submit_grounding` call — the
    caller treats that as a judge failure and falls back to numeric-only grounding rather
    than crashing the run.
    """
    response = await llm.complete(
        model=model,
        system=_load_judge_prompt(),
        messages=_build_messages(response_text, tool_outputs),
        tools=[SUBMIT_GROUNDING_TOOL],
        tool_choice={"type": "tool", "name": "submit_grounding"},
        max_tokens=1500,
    )
    usage = summarize_usage(model, response)
    block = next(
        (b for b in response.content if b.type == "tool_use" and b.name == "submit_grounding"),
        None,
    )
    if block is None:
        raise ValueError(f"Judge did not call submit_grounding. Content types: {[b.type for b in response.content]}")
    raw = block.input if isinstance(block.input, dict) else {}
    try:
        judgement = GroundingJudgement.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"Judge produced an invalid judgement: {e}") from e
    return JudgeResult(claims=judgement.claims, usage=usage)
