from __future__ import annotations

import pytest
from bioforge.agent.planner import SUBMIT_PLAN_TOOL, Plan, _load_planner_prompt, make_plan
from bioforge.tools.registry import list_tools


async def test_make_plan_returns_validated_plan(fake_llm_factory, make_submit_plan_response, multi_step_plan) -> None:
    plan_dict = multi_step_plan(
        [
            ("reverse_complement", "Compute reverse complement of the input."),
            ("gc_content", "Compute GC content of the reverse-complemented sequence."),
        ],
        summary="Reverse-complement, then GC.",
    )
    llm = fake_llm_factory([make_submit_plan_response(plan_dict)])

    result = await make_plan(
        "GC content of the reverse complement of ATGCATGC",
        llm=llm,
        model="claude-sonnet-4-6",
        available_tools=list_tools(),
    )

    assert isinstance(result.plan, Plan)
    assert result.plan.is_trivial is False
    assert len(result.plan.steps) == 2
    assert result.plan.steps[0].expected_tool == "reverse_complement"
    assert result.plan.steps[1].expected_tool == "gc_content"
    assert result.usage.input_tokens > 0


async def test_planner_forces_submit_plan_tool(fake_llm_factory, make_submit_plan_response, trivial_plan) -> None:
    llm = fake_llm_factory([make_submit_plan_response(trivial_plan(tool_name="gc_content"))])

    await make_plan("GC content of ATGC", llm=llm, model="claude-sonnet-4-6", available_tools=list_tools())

    call = llm.calls[0]
    assert call.tool_choice == {"type": "tool", "name": "submit_plan"}
    assert call.tools == [SUBMIT_PLAN_TOOL]


async def test_planner_raises_when_model_does_not_call_submit_plan(fake_llm_factory, make_text_response) -> None:
    llm = fake_llm_factory([make_text_response("I refuse to plan.")])
    with pytest.raises(ValueError, match="did not call submit_plan"):
        await make_plan("anything", llm=llm, model="claude-sonnet-4-6", available_tools=list_tools())


async def test_planner_raises_on_invalid_plan_payload(fake_llm_factory, make_submit_plan_response) -> None:
    llm = fake_llm_factory([make_submit_plan_response({"is_trivial": "not-a-bool"})])
    with pytest.raises(ValueError, match="invalid Plan"):
        await make_plan("anything", llm=llm, model="claude-sonnet-4-6", available_tools=list_tools())


async def test_planner_tools_block_lists_available_tools(
    fake_llm_factory, make_submit_plan_response, trivial_plan
) -> None:
    llm = fake_llm_factory([make_submit_plan_response(trivial_plan(tool_name="gc_content"))])
    await make_plan(
        "GC content of ATGC",
        llm=llm,
        model="claude-sonnet-4-6",
        available_tools=list_tools(),
    )
    user_content = llm.calls[0].messages[0]["content"]
    assert "gc_content" in user_content
    assert "reverse_complement" in user_content


# --- Composite-workflow recipes (regression guards) ------------------------------


def test_planner_prompt_documents_variant_interpretation_recipe() -> None:
    """The variant interpretation chain is a documented planner pattern, not a tool.

    `interpret_variant` was deliberately NOT shipped as a composite tool — instead,
    the planner is taught to compose parse_vcf → format_hgvs → annotate_variant →
    lookup_clinvar / lookup_dbsnp itself. These assertions lock in that the recipe
    stays in the prompt, since deleting it silently would regress agent behavior.
    """
    prompt = _load_planner_prompt()
    assert "Common composite workflows" in prompt
    assert "Variant interpretation" in prompt
    # All five tools in the chain must be named so the planner knows which to compose.
    for tool in ("parse_vcf", "format_hgvs", "annotate_variant", "lookup_clinvar", "lookup_dbsnp"):
        assert tool in prompt, f"variant-interpretation recipe must reference {tool}"


def test_planner_prompt_explains_why_not_a_composite_tool() -> None:
    """The 'do not collapse this chain' guidance is the load-bearing instruction —
    without it, the agent will shortcut to annotate_variant only and lose curated detail."""
    prompt = _load_planner_prompt()
    assert "Do not collapse this chain" in prompt or "do not collapse this chain" in prompt.lower()
