"""Small helper for Server-Sent Events encoding."""

from __future__ import annotations

import json


def sse_event(data: dict, event: str | None = None, id_: str | None = None) -> str:
    parts: list[str] = []
    if event:
        parts.append(f"event: {event}")
    if id_ is not None:
        parts.append(f"id: {id_}")
    parts.append(f"data: {json.dumps(data, default=str)}")
    parts.append("")  # blank line terminates
    return "\n".join(parts) + "\n"
