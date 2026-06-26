"""Deterministic page-bound sanitizer for schema dicts.

Clamps every section's `page_start` / `page_end` into the valid PDF
range [1, total_pages]. Repairs inverted ranges by setting
`page_end = page_start`. Pure-Python, idempotent — running the
sanitizer twice yields the same result as running it once.

Used by `schema_builder` BEFORE the validator so common Gemini
hallucinations (page_end=999 on a 50-page PDF, page_start=0 on a
1-indexed PDF) auto-fix instead of triggering corrective retries.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _collect_pages(schema_dict: dict) -> list[int]:
    """Gather every page_start/page_end int across sections + excluded (nested)."""
    out: list[int] = []

    def walk(nodes):
        for n in nodes or []:
            if not isinstance(n, dict):
                continue
            for k in ("page_start", "page_end"):
                v = n.get(k)
                if _is_int(v):
                    out.append(v)
            walk(n.get("subsections") or [])

    walk(schema_dict.get("sections") or [])
    walk(schema_dict.get("excluded_sections") or [])
    return out


def _detect_print_offset(schema_dict: dict, total_pages: int) -> int:
    """Detect a systematic PRINTED-page-number offset; return amount to subtract.

    When a mid-book chapter is saved as a standalone PDF, Gemini sometimes
    reports the book's PRINTED page numbers (e.g. 96–117) instead of the PDF's
    physical indices (1–22). The plain clamp would then crush every value > N
    down to N (all sections collapse onto the last page). Instead, if the whole
    set of page numbers is a contiguous band sitting ABOVE the page count but
    that would FIT inside [1, total_pages] once shifted down, we shift it — the
    printed→physical conversion (96–117 → 1–22).

    Returns 0 (no shift) for:
      • normal in-range books (max ≤ total_pages) — never triggers,
      • a lone hallucinated outlier (page 999 among 1–8) — its span far exceeds
        total_pages, so it's left to the clamp.
    """
    pages = _collect_pages(schema_dict)
    if not pages:
        return 0
    pmin, pmax = min(pages), max(pages)
    # Only when the top of the band actually exceeds the PDF length.
    if pmax <= total_pages:
        return 0
    # ...and the band is shifted above page 1 and would fit once shifted down.
    span = pmax - pmin + 1
    if pmin > 1 and span <= total_pages:
        return pmin - 1
    return 0


def _shift_pages(schema_dict: dict, offset: int) -> int:
    """Subtract `offset` from every page_start/page_end. Returns count changed."""
    n = 0

    def walk(nodes):
        nonlocal n
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            for k in ("page_start", "page_end"):
                v = node.get(k)
                if _is_int(v):
                    node[k] = v - offset
                    n += 1
            walk(node.get("subsections") or [])

    walk(schema_dict.get("sections") or [])
    walk(schema_dict.get("excluded_sections") or [])
    return n


def _clamp_pair(
    section: dict,
    total_pages: int,
) -> int:
    """Clamp one section's page_start/page_end. Returns count of fields changed."""
    n_fixes = 0
    ps = section.get("page_start")
    pe = section.get("page_end")

    if _is_int(ps):
        new_ps = max(1, min(ps, total_pages))
        if new_ps != ps:
            section["page_start"] = new_ps
            n_fixes += 1
            ps = new_ps
    if _is_int(pe):
        new_pe = max(1, min(pe, total_pages))
        if new_pe != pe:
            section["page_end"] = new_pe
            n_fixes += 1
            pe = new_pe

    # Repair inverted range (only after individual clamps).
    if _is_int(ps) and _is_int(pe) and pe < ps:
        section["page_end"] = ps
        n_fixes += 1

    return n_fixes


def clamp_pages_to_bounds(
    schema_dict: dict,
    total_pages: int | None,
) -> tuple[dict, int]:
    """Clamp every section/excluded entry's pages into [1, total_pages].

    Walks `sections[]` and `excluded_sections[]` recursively (including
    nested `subsections`). Mutates the dict in place and also returns
    it for caller convenience.

    No-ops if `total_pages` is None or non-positive (we have no bound
    to clamp against). Pages already within range pass through; only
    out-of-bounds and inverted-range values are touched.
    """
    if not isinstance(schema_dict, dict):
        return schema_dict, 0
    if total_pages is None or not isinstance(total_pages, int) or total_pages <= 0:
        return schema_dict, 0

    # PRINTED-page-offset pre-pass (runs BEFORE the clamp): if Gemini emitted
    # the book's printed page numbers for a mid-book chapter (e.g. 96–117 in a
    # 22-page PDF), shift the whole band down to physical [1, N] instead of
    # letting the clamp crush every out-of-range value onto the last page.
    # No-op for in-range books and lone outliers (see _detect_print_offset).
    offset = _detect_print_offset(schema_dict, total_pages)
    if offset > 0:
        n_shifted = _shift_pages(schema_dict, offset)
        logger.info(
            "page-offset detected: shifted %d page value(s) by -%d "
            "(printed page numbers → physical 1-%d)",
            n_shifted, offset, total_pages,
        )

    n_total = 0

    def walk(nodes):
        nonlocal n_total
        for n in nodes or []:
            if not isinstance(n, dict):
                continue
            n_total += _clamp_pair(n, total_pages)
            walk(n.get("subsections") or [])

    walk(schema_dict.get("sections") or [])
    walk(schema_dict.get("excluded_sections") or [])
    return schema_dict, n_total


__all__ = ["clamp_pages_to_bounds"]
