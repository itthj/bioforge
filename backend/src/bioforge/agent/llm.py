"""Thin async wrapper around the Anthropic SDK.

Centralizes:
- Client construction with API key from settings
- Pricing table for cost accounting
- A single `complete()` entry point the agent loop uses
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import anthropic
from anthropic.types import Message

from bioforge.config import settings

# Pricing per 1M tokens, USD. Source: https://platform.claude.com/docs/en/pricing
# Keep this table tight — every model the agent calls needs an entry, and a missing entry
# returns 0.0 cost (silent), which would mask billing surprises.
_PRICING_PER_MTOK: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {
        "input": 3.00,
        "output": 15.00,
        "cache_write_5m": 3.75,  # 1.25× input
        "cache_write_1h": 6.00,  # 2× input
        "cache_read": 0.30,  # 0.1× input
    },
    "claude-opus-4-7": {
        "input": 5.00,
        "output": 25.00,
        "cache_write_5m": 6.25,
        "cache_write_1h": 10.00,
        "cache_read": 0.50,
    },
    "claude-haiku-4-5": {
        "input": 1.00,
        "output": 5.00,
        "cache_write_5m": 1.25,
        "cache_write_1h": 2.00,
        "cache_read": 0.10,
    },
}


@dataclass
class UsageSummary:
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    cost_usd: float
    model: str

    def merge(self, other: "UsageSummary") -> "UsageSummary":
        if self.model != other.model:
            raise ValueError(
                f"Cannot merge usage across models: {self.model!r} vs {other.model!r}"
            )
        return UsageSummary(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_tokens=self.cache_creation_tokens + other.cache_creation_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cost_usd=round(self.cost_usd + other.cost_usd, 6),
            model=self.model,
        )

    @classmethod
    def zero(cls, model: str) -> "UsageSummary":
        return cls(
            input_tokens=0,
            output_tokens=0,
            cache_creation_tokens=0,
            cache_read_tokens=0,
            cost_usd=0.0,
            model=model,
        )


def compute_cost(model: str, usage: Any) -> float:
    """Compute USD cost from an Anthropic `Usage` object. Returns 0.0 for unpriced models."""
    rates = _PRICING_PER_MTOK.get(model)
    if rates is None:
        return 0.0
    cache_write = (
        getattr(usage, "cache_creation_input_tokens", 0) or 0
    )
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cost = (
        usage.input_tokens * rates["input"]
        + usage.output_tokens * rates["output"]
        + cache_write * rates["cache_write_5m"]
        + cache_read * rates["cache_read"]
    ) / 1_000_000.0
    return round(cost, 6)


def summarize_usage(model: str, message: Message) -> UsageSummary:
    return UsageSummary(
        input_tokens=message.usage.input_tokens,
        output_tokens=message.usage.output_tokens,
        cache_creation_tokens=getattr(message.usage, "cache_creation_input_tokens", 0) or 0,
        cache_read_tokens=getattr(message.usage, "cache_read_input_tokens", 0) or 0,
        cost_usd=compute_cost(model, message.usage),
        model=model,
    )


class LLM:
    """Async Anthropic client wrapper.

    Held by the agent loop. Built-in retry/backoff comes from the SDK (default 2 retries on
    429 / 5xx). Override via `max_retries=` at construction if needed.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: anthropic.AsyncAnthropic | None = None,
        max_retries: int = 2,
    ) -> None:
        if client is not None:
            self._client = client
        else:
            key = api_key or settings.anthropic_api_key
            if not key:
                raise RuntimeError(
                    "ANTHROPIC_API_KEY is not set. Add it to .env or pass api_key=."
                )
            self._client = anthropic.AsyncAnthropic(api_key=key, max_retries=max_retries)

    async def complete(
        self,
        *,
        model: str,
        system: list[dict] | str,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: dict | None = None,
        max_tokens: int = 4096,
    ) -> Message:
        """`tool_choice` accepts `{"type": "auto"}`, `{"type": "any"}`, or
        `{"type": "tool", "name": ...}` for forced structured output."""
        kwargs: dict[str, Any] = {
            "model": model,
            "system": system,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        return await self._client.messages.create(**kwargs)
