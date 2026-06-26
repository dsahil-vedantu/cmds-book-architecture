"""Anthropic provider — default for digital/mixed PDFs.

Runs P3 (Raw Text Extraction) with hard guardrails.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from app.core.claude_client import extract_text, get_claude, messages_create
from app.core.config import settings
from app.providers.base import OCRProvider
from app.services.prompt_loader import load_raw

logger = logging.getLogger(__name__)


class AnthropicProvider(OCRProvider):
    name = "anthropic"
    handles = ["digital", "mixed"]
    avg_time_per_page = 3.0

    async def extract_text(
        self,
        pdf_bytes: bytes,
        options: dict[str, Any] | None = None,
    ) -> str:
        b64 = base64.standard_b64encode(pdf_bytes).decode()
        system_prompt = load_raw("raw_extract")

        response = await messages_create(
            max_tokens=8000,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": "Extract all text from this PDF verbatim. Preserve headings.",
                        },
                    ],
                }
            ],
        )
        return extract_text(response)

    async def health_check(self) -> bool:
        if settings.anthropic_use_mock:
            return True  # mock mode is always "healthy"
        try:
            client = get_claude()
            await client.messages.create(
                model=settings.ANTHROPIC_HAIKU_MODEL,
                max_tokens=10,
                messages=[{"role": "user", "content": "ping"}],
            )
            return True
        except Exception as e:  # pragma: no cover — defensive
            logger.warning("Anthropic health_check failed: %s", e)
            return False
