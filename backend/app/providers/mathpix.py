"""Mathpix provider — equations, math-heavy PDFs, tables."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from app.providers.base import OCRProvider

logger = logging.getLogger(__name__)

BASE_URL = "https://api.mathpix.com/v3"


class MathpixProvider(OCRProvider):
    name = "mathpix"
    handles = ["equations", "math", "tables"]
    avg_time_per_page = 2.0

    def __init__(self, app_id: str, app_key: str):
        if not app_id or not app_key:
            raise ValueError("Mathpix requires app_id and app_key")
        self.headers = {"app_id": app_id, "app_key": app_key}

    async def extract_text(
        self, pdf_bytes: bytes, options: dict[str, Any] | None = None
    ) -> str:
        async with httpx.AsyncClient(timeout=120) as client:
            files = {"file": ("document.pdf", pdf_bytes, "application/pdf")}
            data = {
                "options_json": json.dumps(
                    {
                        "conversion_formats": {"mmd": True},
                        "math_inline_delimiters": ["$", "$"],
                        "math_display_delimiters": ["$$", "$$"],
                        "rm_spaces": True,
                    }
                )
            }
            upload = await client.post(
                f"{BASE_URL}/pdf", headers=self.headers, files=files, data=data
            )
            upload.raise_for_status()
            pdf_id = upload.json()["pdf_id"]

            # Poll up to 5 minutes for completion
            for _ in range(60):
                status_resp = await client.get(
                    f"{BASE_URL}/pdf/{pdf_id}", headers=self.headers
                )
                status_resp.raise_for_status()
                if status_resp.json().get("status") == "completed":
                    break
                await asyncio.sleep(5)
            else:
                raise TimeoutError(f"Mathpix conversion timed out (pdf_id={pdf_id})")

            result = await client.get(
                f"{BASE_URL}/pdf/{pdf_id}.mmd", headers=self.headers
            )
            result.raise_for_status()
            return result.text

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{BASE_URL}/account", headers=self.headers)
                return r.status_code == 200
        except Exception as e:
            logger.warning("Mathpix health_check failed: %s", e)
            return False
