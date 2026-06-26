"""PDF preflight checker — runs BEFORE schema generation.

Catches edge-case PDFs early so the schema worker doesn't waste a
3-5 minute Gemini call on something that was never going to work:

  * Encrypted PDFs → fail fast with clear error
  * Zero-page or corrupt PDFs → fail fast
  * Rotated pages → flag for auto-rotation
  * Scanned vs digital → drives schema timeout (180s vs 600s)
  * Page count → drives chunking decision (future M3 work)

Pure CPU work via PyMuPDF. No Gemini call. Sub-second.

Return value is a `PreflightResult` dataclass — caller decides what
to do based on `ok` flag and `metadata` dict.

Used by `services/schema_builder.py` (SCHEMA Week 2 wiring).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


PdfType = Literal["digital", "scanned", "image_only", "unknown"]


@dataclass(frozen=True)
class PreflightResult:
    """Result of running pre-flight checks on a PDF."""

    ok: bool
    """True if PDF is safe to send to the schema worker."""

    error: str | None
    """User-facing error message when ok is False. None when ok."""

    pdf_type: PdfType
    """digital = has selectable text; scanned = image-only OCR needed;
    image_only = no text layer at all; unknown = couldn't determine."""

    total_pages: int
    """Number of pages. 0 means PDF is empty/corrupt."""

    has_rotated_pages: bool
    """True if any page has non-zero rotation. Caller may auto-rotate."""

    rotation_pages: list[int]
    """1-indexed page numbers that are rotated."""

    is_encrypted: bool
    """True if PDF is password-protected. Today we can't decrypt; ok=False."""

    recommended_timeout_s: int
    """Suggested Gemini timeout for this PDF type. Plumbs into
    schema_builder. Digital: 180s, Scanned: 600s, Edge cases: 300s."""

    metadata: dict
    """Extracted PDF metadata: title, author, subject, creator,
    producer, creation_date. Empty dict if not extractable."""


def run_preflight(pdf_bytes: bytes) -> PreflightResult:
    """Run all preflight checks on the given PDF bytes.

    Never raises — every failure mode is returned as ok=False with a
    user-facing error message.
    """
    # Local import keeps the module importable even if pymupdf is missing
    # (e.g. minimal test environments). Failure surfaces at call time.
    try:
        import pymupdf
    except ImportError:
        return PreflightResult(
            ok=False,
            error="PyMuPDF (pymupdf) not installed; cannot preflight.",
            pdf_type="unknown",
            total_pages=0,
            has_rotated_pages=False,
            rotation_pages=[],
            is_encrypted=False,
            recommended_timeout_s=300,
            metadata={},
        )

    if not pdf_bytes:
        return PreflightResult(
            ok=False,
            error="PDF is empty (0 bytes).",
            pdf_type="unknown",
            total_pages=0,
            has_rotated_pages=False,
            rotation_pages=[],
            is_encrypted=False,
            recommended_timeout_s=300,
            metadata={},
        )

    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        return PreflightResult(
            ok=False,
            error=f"Cannot open PDF: {type(e).__name__}: {e}",
            pdf_type="unknown",
            total_pages=0,
            has_rotated_pages=False,
            rotation_pages=[],
            is_encrypted=False,
            recommended_timeout_s=300,
            metadata={},
        )

    try:
        is_encrypted = bool(doc.needs_pass) if hasattr(doc, "needs_pass") else False
        if is_encrypted:
            return PreflightResult(
                ok=False,
                error=(
                    "PDF is password-protected. Please remove the password "
                    "before uploading."
                ),
                pdf_type="unknown",
                total_pages=0,
                has_rotated_pages=False,
                rotation_pages=[],
                is_encrypted=True,
                recommended_timeout_s=300,
                metadata={},
            )

        total_pages = doc.page_count
        if total_pages == 0:
            return PreflightResult(
                ok=False,
                error="PDF has zero pages.",
                pdf_type="unknown",
                total_pages=0,
                has_rotated_pages=False,
                rotation_pages=[],
                is_encrypted=False,
                recommended_timeout_s=300,
                metadata={},
            )

        # Rotation detection — collect 1-indexed pages with non-zero rotation
        rotation_pages: list[int] = []
        for page_idx in range(total_pages):
            page = doc.load_page(page_idx)
            if page.rotation:
                rotation_pages.append(page_idx + 1)

        # PDF type detection: sample up to first 5 pages, check text layer
        # presence. Digital = text extractable everywhere. Scanned = no
        # text or near-zero. Image-only = strictly no text.
        sample_count = min(5, total_pages)
        text_word_counts: list[int] = []
        for page_idx in range(sample_count):
            page = doc.load_page(page_idx)
            text = page.get_text("text") or ""
            text_word_counts.append(len(text.split()))

        avg_words_per_page = (
            sum(text_word_counts) / len(text_word_counts)
            if text_word_counts else 0
        )

        # Heuristic thresholds. A digital textbook page is typically
        # 200-500 words. A scanned page returns 0-30 (OCR noise). We
        # split conservatively: 50+ avg words = digital.
        if avg_words_per_page >= 50:
            pdf_type: PdfType = "digital"
            recommended_timeout = 180
        elif avg_words_per_page > 0:
            pdf_type = "scanned"
            recommended_timeout = 600
        else:
            pdf_type = "image_only"
            recommended_timeout = 600

        # Metadata — safe-extract; pymupdf may return None or empty.
        meta_raw = dict(doc.metadata or {})
        metadata = {
            "title": meta_raw.get("title") or None,
            "author": meta_raw.get("author") or None,
            "subject": meta_raw.get("subject") or None,
            "creator": meta_raw.get("creator") or None,
            "producer": meta_raw.get("producer") or None,
            "creation_date": meta_raw.get("creationDate") or None,
        }
        # Strip empty values for cleaner JSON
        metadata = {k: v for k, v in metadata.items() if v}

        return PreflightResult(
            ok=True,
            error=None,
            pdf_type=pdf_type,
            total_pages=total_pages,
            has_rotated_pages=bool(rotation_pages),
            rotation_pages=rotation_pages,
            is_encrypted=False,
            recommended_timeout_s=recommended_timeout,
            metadata=metadata,
        )
    finally:
        doc.close()


__all__ = ["PreflightResult", "PdfType", "run_preflight"]
