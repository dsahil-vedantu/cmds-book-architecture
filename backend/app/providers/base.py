"""OCRProvider abstract base class.

Matches the interface described in 05_OCR_PROVIDERS.md. Sprint 1 only
implements the Anthropic provider; others arrive in Sprint 4.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar


class OCRProvider(ABC):
    name: ClassVar[str]
    handles: ClassVar[list[str]]
    avg_time_per_page: ClassVar[float]

    @abstractmethod
    async def extract_text(
        self,
        pdf_bytes: bytes,
        options: dict[str, Any] | None = None,
    ) -> str:
        """Extract plain text from PDF, preserving structure."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if provider API is reachable and credentials valid."""
