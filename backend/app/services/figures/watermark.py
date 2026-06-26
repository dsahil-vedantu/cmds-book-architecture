"""Watermark cleanup service — wraps the standalone clean_watermark.py
pipeline as an in-process Python function.

Single image -> Gemini image-out with the watermark-removal prompt ->
cleaned PNG bytes.

Mirrors clean_watermark() in upstream clean_watermark.py exactly.

Environment knobs:
  - FIGURE_IMAGE_MODEL     default: "gemini-3.1-flash-image-preview"
  - GEMINI_API_KEY         required (fallback: EXTERNAL_GEMINI_API_KEY)
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

PROMPT_PATH = (
    Path(__file__).resolve().parents[3]
    / "prompts" / "v1" / "figures" / "figure_watermark_removal.txt"
)


def _api_key() -> str:
    key = (
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("EXTERNAL_GEMINI_API_KEY")
        or ""
    )
    if not key:
        try:
            from app.core.config import settings as _settings
            key = _settings.GEMINI_API_KEY or ""
        except Exception:
            key = ""
    return key


def _model_name() -> str:
    return os.environ.get("FIGURE_IMAGE_MODEL", "gemini-3.1-flash-image-preview")


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def clean(
    image_bytes: bytes,
    *,
    model: str | None = None,
    mime_type: str = "image/png",
) -> bytes:
    """Remove faint watermarks from a regenerated figure. Returns PNG bytes."""
    api_key = _api_key()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    target_model = model or _model_name()
    prompt = _load_prompt()

    response = client.models.generate_content(
        model=target_model,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            prompt,
        ],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE", "TEXT"],
        ),
    )
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        raise RuntimeError(f"watermark cleanup: no candidates. Response: {response}")

    parts = getattr(candidates[0].content, "parts", None) or []
    for part in parts:
        inline = getattr(part, "inline_data", None)
        if inline and getattr(inline, "data", None):
            data = inline.data
            if isinstance(data, str):
                data = base64.b64decode(data)
            return data

    text_parts = [getattr(p, "text", None) for p in parts if getattr(p, "text", None)]
    raise RuntimeError(
        "No image data in watermark-cleanup response. Text returned: "
        + ("\n".join(text_parts) if text_parts else "<empty>")
    )
