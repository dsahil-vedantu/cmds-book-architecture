"""Figure regeneration service — AI redraw using Gemini image generation.

Loads the original figure image, sends it to Gemini image generation with
a structured prompt, and returns the redrawn image bytes.
"""

from __future__ import annotations

import base64
import logging

from app.services.prompt_loader import render

logger = logging.getLogger(__name__)

# Gemini image generation models (must support image input + image output)
REGEN_MODEL = "gemini-2.0-flash-preview-image-generation"
FALLBACK_MODEL = "gemini-2.0-flash-exp"


async def redraw_figure(
    *,
    image_bytes: bytes,
    figure_number: str | None,
    caption: str | None,
    description: str | None,
    semantic_type: str,
) -> bytes | None:
    """Redraw an educational figure using Gemini image generation.

    Returns the redrawn image bytes (PNG), or None on failure.
    """
    prompt_text = render(
        "figure_regenerator",
        semantic_type=semantic_type or "diagram",
        caption=caption or "Educational figure",
        description=description or "An educational diagram.",
    )

    try:
        return await _redraw_with_gemini(image_bytes, prompt_text, REGEN_MODEL)
    except Exception as exc:
        logger.warning("Primary redraw model failed (%s): %s — trying fallback", REGEN_MODEL, exc)

    try:
        return await _redraw_with_gemini(image_bytes, prompt_text, FALLBACK_MODEL)
    except Exception as exc2:
        logger.error("Fallback image generation also failed: %s", exc2)
        return None


async def _redraw_with_gemini(image_bytes: bytes, prompt_text: str, model: str) -> bytes:
    """Use Gemini multimodal image generation to redraw the figure."""
    from google import genai
    from google.genai import types

    from app.core.config import settings

    client = genai.Client(api_key=settings.GEMINI_API_KEY)

    b64 = base64.b64encode(image_bytes).decode()

    response = await client.aio.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
            types.Part.from_text(prompt_text),
        ],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE", "TEXT"],
            temperature=0.4,
        ),
    )

    # Extract image bytes from response
    for part in response.candidates[0].content.parts:
        if part.inline_data and part.inline_data.mime_type.startswith("image/"):
            return part.inline_data.data

    raise ValueError("No image part in Gemini response")


