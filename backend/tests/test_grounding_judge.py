"""Layer 4 entity/mechanistic judge (BioForge v4 §4).

The judge is exercised through the FakeLLM so the structured-output contract (forced
`submit_grounding` call -> typed JudgedClaim list) is verified without a network call.
"""

from __future__ import annotations

import pytest
from anthropic.types import Message, ToolUseBlock, Usage
from bioforge.agent.grounding import judge_claims


def submit_grounding_response(claims: list[dict]) -> Message:
    """Build an Anthropic Message that calls submit_grounding with the given claims."""
    return Message(
        id="msg_judge_test",
        type="message",
        role="assistant",
        model="claude-sonnet-4-6",
        content=[ToolUseBlock(type="tool_use", id="toolu_judge", name="submit_grounding", input={"claims": claims})],
        stop_reason="tool_use",
        stop_sequence=None,
        usage=Usage(
            input_tokens=120,
            output_tokens=40,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
            service_tier=None,
        ),
    )


async def test_judge_classifies_and_judges_claims(fake_llm_factory) -> None:
    llm = fake_llm_factory(
        [
            submit_grounding_response(
                [
                    {"text": "BRCA1 is a tumor suppressor", "kind": "background", "status": "background"},
                    {"text": "disrupts the binding domain", "kind": "mechanistic", "status": "unsupported"},
                    {
                        "text": "rs80357065",
                        "kind": "entity",
                        "status": "supported",
                        "cited_field": "colocated_variants[0].id",
                    },
                ]
            )
        ]
    )
    result = await judge_claims(
        response_text="BRCA1 is a tumor suppressor; variant rs80357065 disrupts the binding domain.",
        tool_outputs=[{"colocated_variants": [{"id": "rs80357065"}]}],
        llm=llm,
        model="claude-sonnet-4-6",
    )
    assert {c.kind for c in result.claims} == {"background", "mechanistic", "entity"}
    unsupported = [c for c in result.claims if c.status == "unsupported"]
    assert [c.text for c in unsupported] == ["disrupts the binding domain"]
    supported = next(c for c in result.claims if c.status == "supported")
    assert supported.cited_field == "colocated_variants[0].id"
    assert result.usage.input_tokens == 120


async def test_judge_empty_claim_list_is_valid(fake_llm_factory) -> None:
    llm = fake_llm_factory([submit_grounding_response([])])
    result = await judge_claims(
        response_text="The reverse complement is ACGT.",
        tool_outputs=[{"reverse_complement": "ACGT"}],
        llm=llm,
        model="claude-sonnet-4-6",
    )
    assert result.claims == []


async def test_judge_raises_when_model_skips_the_tool(fake_llm_factory, make_text_response) -> None:
    llm = fake_llm_factory([make_text_response("I'd rather not call the tool.")])
    with pytest.raises(ValueError, match="submit_grounding"):
        await judge_claims(response_text="x", tool_outputs=[], llm=llm, model="claude-sonnet-4-6")
