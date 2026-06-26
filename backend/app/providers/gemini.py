"""Gemini provider — handles ALL PDF types for P3 raw text extraction.

Gemini 2.5 Pro reads PDFs natively (digital, scanned, image-based),
avoiding the OCR corruption issues seen with Claude's base64 pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import Any

from app.providers.base import OCRProvider
from app.services.prompt_loader import load_raw

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


def _extract_text_sync(pdf_bytes: bytes) -> str:
    """Synchronous Gemini PDF→text extraction. Runs in a thread via asyncio.to_thread."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=_get_api_key())
    prompt = load_raw("raw_extract")

    tmp_path = None
    uploaded_file = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        with open(tmp_path, "rb") as f:
            uploaded_file = client.files.upload(
                file=f,
                config=types.UploadFileConfig(
                    mime_type="application/pdf",
                    display_name="textbook_raw.pdf",
                ),
            )
        logger.info("Gemini raw-extract file uploaded: %s", uploaded_file.name)

        response = client.models.generate_content(
            model="gemini-2.5-pro",
            contents=[
                types.Part.from_uri(
                    file_uri=uploaded_file.uri,
                    mime_type="application/pdf",
                ),
                prompt,
            ],
            config=types.GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=16000,
            ),
        )
        return response.text or ""

    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        if uploaded_file is not None:
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception:
                pass


class GeminiProvider(OCRProvider):
    name = "gemini"
    handles = ["digital", "scanned", "mixed", "image"]
    avg_time_per_page = 2.0

    async def extract_text(
        self,
        pdf_bytes: bytes,
        options: dict[str, Any] | None = None,
    ) -> str:
        return await asyncio.to_thread(_extract_text_sync, pdf_bytes)

    async def health_check(self) -> bool:
        try:
            _get_api_key()
            return True
        except Exception:
            return False
