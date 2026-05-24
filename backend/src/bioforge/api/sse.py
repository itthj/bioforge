"""Server-Sent Events helpers for the streaming agent endpoints.

SSE format (per WHATWG spec):

    event: <event-name>
    data: <utf-8 line>
    data: <more utf-8 if multi-line>
    [blank line]

Browsers (and `httpx.stream` / `curl -N`) treat each blank-line-separated block as one
event. Comment lines (`: ping`) are ignored by consumers but keep proxies from closing
idle connections during long-running BLAST calls.
"""

from __future__ import annotations

import json
from typing import Any


def format_event(event: str, data: dict | str) -> str:
    """Render one SSE event as a UTF-8 string ready to write to the wire.

    `data` is JSON-encoded if dict, sent verbatim if str. Newlines in string payloads
    are escaped into per-line `data:` continuations per the SSE spec.
    """
    if isinstance(data, dict):
        payload = json.dumps(data, default=_json_default)
    else:
        payload = str(data)
    lines = payload.split("\n")
    data_block = "\n".join(f"data: {line}" for line in lines)
    return f"event: {event}\n{data_block}\n\n"


def format_keepalive() -> str:
    """Comment line — consumers ignore it; proxies see traffic and stay open."""
    return ": keepalive\n\n"


def _json_default(obj: Any) -> Any:
    """Fallback JSON encoder for objects with `.isoformat()` (datetimes) or other
    non-builtins that occasionally land in agent steps."""
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
