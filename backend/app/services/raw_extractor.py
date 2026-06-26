"""P3 Raw Text Extractor — delegates to an OCR provider; validates coverage.

The provider is chosen by the caller (Sprint 4 adds auto-routing). For Sprint 2
we always use Anthropic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.providers.base import OCRProvider

logger = logging.getLogger(__name__)

COVERAGE_THRESHOLD = 0.65


@dataclass
class RawTextResult:
    text: str
    word_count: int
    coverage_ratio: float  # ext words / analyser estimate
    coverage_warning: bool
    provider_name: str


async def extract_raw_text(
    *,
    pdf_bytes: bytes,
    provider: OCRProvider,
    estimated_words: int | None = None,
) -> RawTextResult:
    text = await provider.extract_text(pdf_bytes)
    words = len(text.split()) if text else 0

    if estimated_words and estimated_words > 0:
        ratio = words / estimated_words
    else:
        ratio = 1.0

    warn = bool(estimated_words) and ratio < COVERAGE_THRESHOLD
    if warn:
        logger.warning(
            "Raw text coverage low: %s/%s (%.1f%%) via provider=%s",
            words,
            estimated_words,
            ratio * 100,
            provider.name,
        )

    return RawTextResult(
        text=text,
        word_count=words,
        coverage_ratio=ratio,
        coverage_warning=warn,
        provider_name=provider.name,
    )
