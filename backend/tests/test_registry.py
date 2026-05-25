from __future__ import annotations

import pytest
from bioforge.tools.base import ToolInput, ToolOutput
from bioforge.tools.registry import (
    REGISTRY,
    execute_tool,
    get_tool,
    list_tools,
    register_tool,
    to_anthropic_tools,
)


def test_gc_content_is_registered_on_import() -> None:
    spec = get_tool("gc_content")
    assert spec.name == "gc_content"
    assert spec.cost_hint == "cheap"
    assert spec.destructive is False
    assert "sequence" in spec.tags


def test_to_anthropic_tools_has_required_fields() -> None:
    tools = to_anthropic_tools()
    assert len(tools) >= 1
    for t in tools:
        assert isinstance(t["name"], str)
        assert isinstance(t["description"], str) and t["description"]
        schema = t["input_schema"]
        assert schema["type"] == "object"
        assert "properties" in schema


def test_to_anthropic_tools_attaches_cache_control_to_last() -> None:
    tools = to_anthropic_tools(cache_last=True)
    assert tools[-1]["cache_control"] == {"type": "ephemeral"}
    # Earlier tools (if any) should not have it
    for t in tools[:-1]:
        assert "cache_control" not in t


def test_to_anthropic_tools_no_cache_when_disabled() -> None:
    tools = to_anthropic_tools(cache_last=False)
    for t in tools:
        assert "cache_control" not in t


def test_get_tool_raises_for_unknown() -> None:
    with pytest.raises(KeyError, match="not registered"):
        get_tool("nonexistent_tool_xyz")


def test_list_tools_filters_by_tag() -> None:
    sequence_tools = list_tools(tags=["sequence"])
    assert all("sequence" in t.tags for t in sequence_tools)
    empty = list_tools(tags=["does-not-exist"])
    assert empty == []


def test_double_registration_is_rejected() -> None:
    class FakeIn(ToolInput):
        x: int

    class FakeOut(ToolOutput):
        y: int

    async def _handler(_inp: FakeIn) -> FakeOut:  # pragma: no cover — never called
        return FakeOut(y=0)

    register_tool(
        name="_test_double_reg",
        description="test",
        input_model=FakeIn,
        output_model=FakeOut,
        version="0.0.0",
    )(_handler)
    try:
        with pytest.raises(ValueError, match="already registered"):
            register_tool(
                name="_test_double_reg",
                description="test",
                input_model=FakeIn,
                output_model=FakeOut,
                version="0.0.0",
            )(_handler)
    finally:
        REGISTRY.pop("_test_double_reg", None)


async def test_execute_tool_validates_input_and_stamps_provenance() -> None:
    output = await execute_tool("gc_content", {"sequence": "ATGCATGC"})
    assert output.tool_name == "gc_content"
    assert output.tool_version == "1.0.0"
    assert "Biopython" in " ".join(output.citations)


async def test_execute_tool_rejects_bad_input() -> None:
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        await execute_tool("gc_content", {"sequence": "ATGZ!"})
