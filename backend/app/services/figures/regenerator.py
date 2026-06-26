"""Figure regeneration service — wraps the standalone regenerate_figures.py
pipeline as an in-process Python function.

Algorithm:
  - Read prompt template (style: enhanced | original)
  - Optionally inject per-figure caption / label metadata as a context block
  - Optionally inject a global JSON reference context block
  - Optionally append user-supplied custom_instructions
  - Send (image bytes + composed prompt) to Gemini image-out model
  - Decode base64 (or raw) inline_data from response → return PNG bytes

Mirrors process_image_to_png() + build_prompt() in upstream
regenerate_figures.py exactly.

Environment knobs:
  - FIGURE_IMAGE_MODEL     default: "gemini-3.1-flash-image-preview"
  - GEMINI_API_KEY         required (fallback: EXTERNAL_GEMINI_API_KEY)

Per-call overrides (passed to regenerate()):
  - style                  "enhanced" | "original"  (default: enhanced)
  - custom_instructions    free text appended after the style prompt
  - json_context           dict — global reference labels/terms
  - figure_meta            dict — per-figure caption/label/page/context
  - model                  override env default for this call
"""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).resolve().parents[3] / "prompts" / "v1" / "figures"

# Style key -> prompt filename
_STYLES = {
    "enhanced": "figure_regen_enhanced.txt",
    "original": "figure_regen_original.txt",
}


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


def _load_style_prompt(style: str) -> str:
    fname = _STYLES.get(style)
    if not fname:
        raise ValueError(
            f"unknown regen style {style!r}; choose from {sorted(_STYLES)}"
        )
    return (PROMPT_DIR / fname).read_text(encoding="utf-8")


def build_prompt(
    base_prompt: str,
    json_context: dict[str, Any] | None = None,
    figure_meta: dict[str, Any] | None = None,
    custom_instructions: str | None = None,
) -> str:
    """Assemble (context blocks) + base_prompt + (custom instructions).

    Mirrors build_prompt() in upstream regenerate_figures.py — context
    blocks go ABOVE the system prompt so the model treats them as
    grounding before reading the instruction set.
    """
    context_blocks: list[str] = []
    if json_context is not None:
        context_blocks.append(
            "Original JSON Reference Data provided by the user to ensure "
            "100% accurate text and labels:\n"
            + json.dumps(json_context, indent=2, ensure_ascii=False)
        )
    if figure_meta is not None:
        context_blocks.append(
            "This figure's metadata from the upstream extractor "
            "(use the caption verbatim for labels):\n"
            + json.dumps(figure_meta, indent=2, ensure_ascii=False)
        )

    parts: list[str] = []
    if context_blocks:
        parts.append("\n\n".join(context_blocks))
    parts.append(base_prompt)
    if custom_instructions and custom_instructions.strip():
        parts.append(
            "Additional user instructions for this regeneration:\n"
            + custom_instructions.strip()
        )
    return "\n\n".join(parts)


def regenerate(
    image_bytes: bytes,
    *,
    style: str = "enhanced",
    custom_instructions: str | None = None,
    json_context: dict[str, Any] | None = None,
    figure_meta: dict[str, Any] | None = None,
    model: str | None = None,
    mime_type: str = "image/png",
) -> bytes:
    """Generate one regenerated figure. Returns PNG bytes."""
    api_key = _api_key()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")

    from google import genai
    from google.genai import types

    base_prompt = _load_style_prompt(style)
    prompt_text = build_prompt(
        base_prompt,
        json_context=json_context,
        figure_meta=figure_meta,
        custom_instructions=custom_instructions,
    )
    client = genai.Client(api_key=api_key)
    target_model = model or _model_name()

    response = client.models.generate_content(
        model=target_model,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            prompt_text,
        ],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE", "TEXT"],
        ),
    )
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        raise RuntimeError(f"Gemini image-out: no candidates. Response: {response}")

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
        "No image data in Gemini response. Text returned: "
        + ("\n".join(text_parts) if text_parts else "<empty>")
    )
