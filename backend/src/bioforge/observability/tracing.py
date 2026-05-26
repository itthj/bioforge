"""OpenTelemetry tracing for the agent loop.

# Design

The spec marks OTel "required, not optional" — every agent run, every tool call, every
LLM call produces a span. But we don't want the test suite spewing spans to stdout, and
we don't want production deployments paying for export when otel isn't configured.

So:
  - Tracing is OFF by default (`BIOFORGE_OTEL_ENABLED=false`). The OTel API returns
    a no-op tracer in this state; `with tracer.start_as_current_span(...)` is a no-op
    too. Zero perf cost.
  - When enabled, `configure_tracing()` installs a TracerProvider + SimpleSpanProcessor
    with the chosen exporter. Default exporter is `console` (writes to stdout, useful
    for local dev). `none` configures the provider but doesn't export — useful for
    tests that want to capture spans via their own InMemorySpanExporter.

# Span shape

All spans are children of a root `agent.run` span. The hierarchy mirrors the loop:

    agent.run                       attrs: bioforge.goal, .project_id, .model, .status
      ├─ agent.plan                 attrs: bioforge.is_replan, .plan_size
      │    └─ llm.complete          attrs: gen_ai.* tokens, model
      ├─ agent.approval_gate        attrs: bioforge.approval_required
      ├─ agent.execute              attrs: bioforge.iteration
      │    ├─ llm.complete
      │    └─ tool.call             attrs: bioforge.tool_name, .duration_ms
      ├─ agent.critique
      │    └─ llm.complete
      ├─ agent.replan (if needed)
      └─ ...

# Semantic conventions

For LLM spans we follow the OpenTelemetry GenAI conventions where they're stable:
  - `gen_ai.system` = "anthropic"
  - `gen_ai.request.model`
  - `gen_ai.response.model`
  - `gen_ai.usage.input_tokens`
  - `gen_ai.usage.output_tokens`

BioForge-specific attributes use the `bioforge.*` namespace and are deliberately stable
across phases.
"""

from __future__ import annotations

import threading
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanProcessor,
)

from bioforge.config import settings

_TRACER_NAME = "bioforge.agent"
_configured = False
_lock = threading.Lock()


def _parse_otlp_headers(raw: str) -> dict[str, str]:
    """Parse `k=v,k2=v2` OTLP header config into the shape the exporter expects."""
    headers: dict[str, str] = {}
    for chunk in raw.split(","):
        item = chunk.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError("BIOFORGE_OTEL_HEADERS must use comma-separated key=value pairs")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError("BIOFORGE_OTEL_HEADERS contains an empty header name")
        headers[key] = value.strip()
    return headers


def _build_export_processor(exporter: str) -> SpanProcessor | None:
    """Build the configured exporter processor.

    `console` uses SimpleSpanProcessor so local debugging flushes immediately.
    `otlp` uses BatchSpanProcessor so production ingest does not block the request path.
    `none` installs no exporter but still lets tests add an InMemory exporter.
    """
    chosen = exporter.lower()
    if chosen == "none":
        return None
    if chosen == "console":
        return SimpleSpanProcessor(ConsoleSpanExporter())
    if chosen == "otlp":
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise RuntimeError(
                "BIOFORGE_OTEL_EXPORTER=otlp requires opentelemetry-exporter-otlp-proto-http to be installed"
            ) from exc
        return BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=settings.otel_endpoint,
                headers=_parse_otlp_headers(settings.otel_headers),
            )
        )
    raise ValueError(f"Unsupported BIOFORGE_OTEL_EXPORTER value {exporter!r}; expected 'console', 'none', or 'otlp'")


def configure_tracing(
    *,
    enabled: bool | None = None,
    exporter: str | None = None,
    extra_processors: list[SpanProcessor] | None = None,
) -> None:
    """Set up the OpenTelemetry TracerProvider. Idempotent within a process — the second
    call adds extra processors but does NOT replace the provider (OTel forbids that).

    Tests use `extra_processors` to inject their own InMemorySpanExporter.
    """
    global _configured

    is_enabled = enabled if enabled is not None else settings.otel_enabled
    chosen = (exporter or settings.otel_exporter).lower()

    with _lock:
        if not _configured:
            provider = TracerProvider(
                resource=Resource.create(
                    {
                        "service.name": "bioforge",
                        "service.namespace": "bioforge",
                    }
                )
            )
            if is_enabled:
                processor = _build_export_processor(chosen)
                if processor is not None:
                    provider.add_span_processor(processor)
            for proc in extra_processors or []:
                provider.add_span_processor(proc)
            trace.set_tracer_provider(provider)
            _configured = True
        else:
            # Already configured — only honor extra_processors, since OTel
            # forbids replacing the provider mid-process.
            provider = trace.get_tracer_provider()
            for proc in extra_processors or []:
                if hasattr(provider, "add_span_processor"):
                    provider.add_span_processor(proc)


def get_tracer() -> trace.Tracer:
    """Return the BioForge tracer. Safe to call before `configure_tracing()` — when no
    provider is set, OTel returns a no-op tracer and `start_as_current_span` is a no-op.
    """
    return trace.get_tracer(_TRACER_NAME)


# Convenience module-level handle. Most call sites use this directly so they don't have
# to know about the provider lifecycle.
tracer = get_tracer()


# --- Span attribute helpers ----------------------------------------------------------


def set_agent_run_attrs(span: trace.Span, *, goal: str, project_id: str, model: str) -> None:
    """Stamp the root agent.run span with the conventional attributes."""
    # Truncate the goal — spans don't want multi-KB payloads.
    truncated_goal = goal if len(goal) <= 500 else goal[:497] + "..."
    span.set_attribute("bioforge.goal", truncated_goal)
    span.set_attribute("bioforge.goal_length", len(goal))
    span.set_attribute("bioforge.project_id", project_id)
    span.set_attribute("bioforge.model", model)
    span.set_attribute("gen_ai.system", "anthropic")
    span.set_attribute("gen_ai.request.model", model)


def set_llm_call_attrs(
    span: trace.Span,
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    stop_reason: str | None = None,
) -> None:
    span.set_attribute("gen_ai.system", "anthropic")
    span.set_attribute("gen_ai.request.model", model)
    span.set_attribute("gen_ai.response.model", model)
    span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
    if cache_creation_tokens:
        span.set_attribute("gen_ai.usage.cache_creation_tokens", cache_creation_tokens)
    if cache_read_tokens:
        span.set_attribute("gen_ai.usage.cache_read_tokens", cache_read_tokens)
    if stop_reason:
        span.set_attribute("gen_ai.response.finish_reason", stop_reason)


def set_tool_call_attrs(
    span: trace.Span,
    *,
    tool_name: str,
    tool_version: str = "",
    cost_hint: str = "",
    destructive: bool = False,
    input_size_bytes: int = 0,
) -> None:
    span.set_attribute("bioforge.tool_name", tool_name)
    if tool_version:
        span.set_attribute("bioforge.tool_version", tool_version)
    if cost_hint:
        span.set_attribute("bioforge.tool_cost_hint", cost_hint)
    span.set_attribute("bioforge.tool_destructive", destructive)
    if input_size_bytes:
        span.set_attribute("bioforge.tool_input_size_bytes", input_size_bytes)


def record_exception(span: trace.Span, exc: BaseException) -> None:
    span.record_exception(exc)
    span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))


def set_status_ok(span: trace.Span, *attrs: Any) -> None:
    span.set_status(trace.Status(trace.StatusCode.OK))
