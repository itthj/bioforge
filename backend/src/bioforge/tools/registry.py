from __future__ import annotations

from collections.abc import Awaitable, Callable

from bioforge.tools.base import CostHint, ToolInput, ToolOutput, ToolSpec

REGISTRY: dict[str, ToolSpec] = {}


def register_tool(
    *,
    name: str,
    description: str,
    input_model: type[ToolInput],
    output_model: type[ToolOutput],
    version: str,
    citations: list[str] | None = None,
    cost_hint: CostHint = "cheap",
    destructive: bool = False,
    tags: list[str] | None = None,
) -> Callable[[Callable[..., Awaitable[ToolOutput]]], Callable[..., Awaitable[ToolOutput]]]:
    """Decorator: register an async handler as a bio tool.

    The handler receives a validated `input_model` instance and returns an `output_model`
    instance. Provenance fields (`tool_name`, `tool_version`, `citations`) are stamped on
    the output by the executor — handlers don't set them.
    """

    def decorator(
        handler: Callable[..., Awaitable[ToolOutput]],
    ) -> Callable[..., Awaitable[ToolOutput]]:
        if name in REGISTRY:
            raise ValueError(f"Tool {name!r} already registered")
        REGISTRY[name] = ToolSpec(
            name=name,
            description=description,
            input_model=input_model,
            output_model=output_model,
            handler=handler,
            version=version,
            citations=list(citations or []),
            cost_hint=cost_hint,
            destructive=destructive,
            tags=list(tags or []),
        )
        return handler

    return decorator


def get_tool(name: str) -> ToolSpec:
    try:
        return REGISTRY[name]
    except KeyError as e:
        raise KeyError(f"Tool {name!r} not registered. Known: {sorted(REGISTRY)}") from e


def list_tools(tags: list[str] | None = None) -> list[ToolSpec]:
    if not tags:
        return list(REGISTRY.values())
    tag_set = set(tags)
    return [t for t in REGISTRY.values() if tag_set.intersection(t.tags)]


def to_anthropic_tools(tags: list[str] | None = None, cache_last: bool = True) -> list[dict]:
    """Convert the registry to Anthropic's `tools` array.

    With `cache_last=True`, attaches `cache_control: {"type": "ephemeral"}` to the last
    tool definition. Combined with cache_control on the system prompt, this caches the
    tools + system prefix together (render order is tools → system → messages).

    The 2048-token minimum prefix for Sonnet 4.6 means caching is a no-op in Phase 0 — the
    marker is set so caching activates automatically once Phase 1 tools push the prefix
    over the threshold.
    """
    tools = [t.anthropic_definition() for t in list_tools(tags)]
    if cache_last and tools:
        tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
    return tools


async def execute_tool(name: str, raw_input: dict) -> ToolOutput:
    """Validate `raw_input` against the tool's schema, run the handler, stamp provenance.

    Each call gets its own `tool.call` span so the trace shows tools as children of the
    agent.execute span, including composite tools (find_offtargets → blast nests
    naturally because both calls hit this function)."""
    from bioforge.observability.tracing import (
        record_exception,
        set_tool_call_attrs,
        tracer,
    )

    spec = get_tool(name)
    with tracer.start_as_current_span(f"tool.call.{name}") as span:
        try:
            input_json_size = 0
            try:
                import json as _json

                input_json_size = len(_json.dumps(raw_input))
            except Exception:  # noqa: BLE001
                pass
            set_tool_call_attrs(
                span,
                tool_name=spec.name,
                tool_version=spec.version,
                cost_hint=spec.cost_hint,
                destructive=spec.destructive,
                input_size_bytes=input_json_size,
            )
            validated = spec.input_model.model_validate(raw_input)
            result = await spec.handler(validated)
            if not isinstance(result, spec.output_model):
                raise TypeError(
                    f"Tool {name!r} returned {type(result).__name__}, expected {spec.output_model.__name__}"
                )
            result.tool_name = spec.name
            result.tool_version = spec.version
            if not result.citations:
                result.citations = list(spec.citations)
            return result
        except Exception as e:
            record_exception(span, e)
            raise
