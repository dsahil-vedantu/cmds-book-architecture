"""Sarvam provider — Indian languages (Hindi, Tamil, Telugu, Bengali, etc)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.providers.base import OCRProvider

logger = logging.getLogger(__name__)

BASE_URL = "https://api.sarvam.ai/v1"


class SarvamProvider(OCRProvider):
    name = "sarvam"
    handles = [
        "hindi",
        "tamil",
        "bengali",
        "telugu",
        "marathi",
        "gujarati",
        "kannada",
        "malayalam",
        "punjabi",
        "indic",
    ]
    avg_time_per_page = 4.0

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("Sarvam requires api_key")
        self.headers = {"api-subscription-key": api_key}

    async def extract_text(
        self, pdf_bytes: bytes, options: dict[str, Any] | None = None
    ) -> str:
        language = (options or {}).get("language", "hi-IN")
        async with httpx.AsyncClient(timeout=180) as client:
            files = {"file": ("document.pdf", pdf_bytes, "application/pdf")}
            data = {"language_code": language, "extract_layout": "true"}
            response = await client.post(
                f"{BASE_URL}/document/ocr",
                headers=self.headers,
                files=files,
                data=data,
            )
            response.raise_for_status()
            payload = response.json()
            return payload.get("extracted_text", "") or payload.get("text", "")

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{BASE_URL}/health", headers=self.headers)
                return r.status_code == 200
        except Exception as e:
            logger.warning("Sarvam health_check failed: %s", e)
            return False
