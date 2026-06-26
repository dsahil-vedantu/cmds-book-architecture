"""Gemini text-generation client for regeneration.

Provides the same interface as claude_client.messages_create / extract_text
so the regenerator can swap between backends without logic changes.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


def _get_api_key() -> str:
    api_key = os.environ.get("GEMINI_API_KEY") or ""
    if not api_key:
        try:
            from app.core.config import settings
            api_key = settings.GEMINI_API_KEY
        except Exception:
            pass
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set in .env")
    return api_key


def _get_model() -> str:
    try:
        from app.core.config import settings
        return settings.GEMINI_REGEN_MODEL
    except Exception:
        return "gemini-2.5-flash"


@dataclass
class GeminiResponse:
    text: str


def _build_parts(content: Any) -> list:
    """Convert message content (str or list of parts) into Gemini Part objects."""
    import base64 as _base64
    from google.genai import types

    if isinstance(content, str):
        return [types.Part.from_text(text=content)]

    parts = []
    for item in content:
        if isinstance(item, str):
            parts.append(types.Part.from_text(text=item))
        elif isinstance(item, dict):
            t = item.get("type", "")
            if t == "text":
                parts.append(types.Part.from_text(text=item.get("text", "")))
            elif t == "image_url":
                url = item.get("image_url", {}).get("url", "")
                if url.startswith("data:"):
                    # data:image/png;base64,<data>
                    header, b64data = url.split(",", 1)
                    mime = header.split(":")[1].split(";")[0]
                    raw = _base64.b64decode(b64data)
                    parts.append(types.Part.from_bytes(data=raw, mime_type=mime))
                else:
                    parts.append(types.Part.from_uri(file_uri=url, mime_type="image/png"))
    return parts


def _generate_sync(
    system: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    model: str | None = None,
) -> GeminiResponse:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=_get_api_key())
    if model is None:
        model = _get_model()

    # Build contents from messages list
    contents = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        gemini_role = "model" if role == "assistant" else "user"
        contents.append(types.Content(role=gemini_role, parts=_build_parts(content)))

    config = types.GenerateContentConfig(
        system_instruction=system,
        temperature=0.2,
        max_output_tokens=max_tokens,
    )
    # gemini-2.5-flash requires thinking to be explicitly disabled for non-thinking mode
    if "2.5-flash" in model:
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.2,
            max_output_tokens=max_tokens,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )

    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=config,
    )

    # Extract text from response — handle thinking models that may return None
    text = response.text
    if text is None and response.candidates:
        parts = response.candidates[0].content.parts if response.candidates[0].content else []
        text = "".join(p.text for p in (parts or []) if hasattr(p, "text") and p.text)

    return GeminiResponse(text=text or "")


async def messages_create(
    *,
    max_tokens: int,
    system: str,
    messages: list[dict[str, Any]],
    model: str | None = None,
    **_kwargs: Any,
) -> GeminiResponse:
    """Async wrapper — runs the sync Gemini call in a thread.

    Pass ``model`` to override the default GEMINI_REGEN_MODEL.
    Supports image content parts (base64 data URIs) for multimodal calls.
    """
    return await asyncio.to_thread(_generate_sync, system, messages, max_tokens, model)


def extract_text(response: GeminiResponse) -> str:
    return response.text
