"""P1 Analyser — detect PDF type, page/word count, subject, content features."""

from __future__ import annotations

import base64
import logging

from app.core.claude_client import extract_text, messages_create
from app.schemas.analyser import AnalyserResult
from app.services.prompt_loader import load_raw
from app.utils.json_parse import parse_json

logger = logging.getLogger(__name__)

MAX_TOKENS = 600


async def analyse_pdf(pdf_bytes: bytes) -> AnalyserResult:
    b64 = base64.standard_b64encode(pdf_bytes).decode()
    system_prompt = load_raw("analyser")

    response = await messages_create(
        max_tokens=MAX_TOKENS,
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
                    {"type": "text", "text": "Analyse this PDF. Return JSON metadata."},
                ],
            }
        ],
    )

    text = extract_text(response)
    data = parse_json(text)
    return AnalyserResult(**data)
