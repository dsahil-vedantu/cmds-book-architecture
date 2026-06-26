"""Deterministic page-range computation for theory extraction.

Single source of truth for "which pages of the PDF go to Gemini for this
section, and where should Gemini stop." Replaces the previously scattered
logic in:
  - `extract.py` (the inline `effective_page_end` formula in two places)
  - `theory_extractor._slice_pdf` (silent fallbacks to full PDF)
  - `theory_extractor._build_user_prompt` (title-only STOP anchor that matched
    wrong occurrences on multi-occurrence titles)

Architecture (Theory Worker Unit 1 — see audit for 10-bug context):

  page_start
    - From schema (mandate).
    - If missing → previous section's page_start (deterministic inference).
    - If still nothing → raise SliceComputationError. No silent fallback.

  page_end
    - Container (has subsections) → first child's page_start.
    - Leaf → next section's page_start in DOCUMENT ORDER (Cat A + excluded
      banks count as stop boundaries, even though theory worker doesn't
      EXTRACT them — they just define where this section ends).
    - Last section in chapter → chapter wrapper's page_end (mandate).
    - If neither → raise.

  stop_anchor
    - (title, page) pair instead of title-only. Gemini stops at the heading
      on that exact page, not at earlier same-named occurrences.

No silent fallbacks. Every inference is logged in `diagnostics`. Every
unresolvable case raises `SliceComputationError` so the worker can mark
the section status="failed" with a clear reason instead of silently
extracting wrong content.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.schemas.analyser import BookSchema, ExcludedSection, SchemaSection


class SliceComputationError(Exception):
    """Raised when a section's page range cannot be computed without faking values.

    The theory worker catches this and marks the section status='failed' with
    a diagnostic, rather than silently extracting wrong content.
    """

    def __init__(self, section_id: str, reason: str):
        self.section_id = section_id
        self.reason = reason
        super().__init__(f"slice_unresolvable[{section_id}]: {reason}")


@dataclass(frozen=True)
class SliceSpec:
    """Deterministic slice spec for one section's Gemini call.

    Both `page_start` and `page_end` are always set (never None).
    Validated in __post_init__: 1 <= page_start <= page_end.
    """

    section_id: str
    page_start: int
    page_end: int
    is_container: bool
    # STOP anchor sent to Gemini as a (title, page) pair so Gemini stops at
    # the heading on that exact page (fixes multi-occurrence-title bug).
    # Both None when this is the last section in the document (no stop).
    stop_anchor_title: Optional[str]
    stop_anchor_page: Optional[int]
    # Any inferences the slice engine made (for logging / observability).
    diagnostics: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self):
        if not isinstance(self.page_start, int) or self.page_start < 1:
            raise ValueError(
                f"page_start must be int >= 1, got {self.page_start!r} "
                f"for section {self.section_id}"
            )
        if not isinstance(self.page_end, int) or self.page_end < self.page_start:
            raise ValueError(
                f"page_end must be int >= page_start ({self.page_start}), "
                f"got {self.page_end!r} for section {self.section_id}"
            )


# ─── Internal helpers ────────────────────────────────────────────────────


@dataclass(frozen=True)
class _DocOrderEntry:
    """One heading/section position in PDF reading order."""

    id: Optional[str]   # section.id; None for excluded entries
    title: str
    page_start: int
    kind: str           # 'section' | 'excluded'


def _flatten_document_order(schema: BookSchema) -> list[_DocOrderEntry]:
    """All section + excluded headings in document order, sorted by page_start.

    Excluded banks (and nested excluded sub-banks) are included because they
    act as stop boundaries for theory extraction — a theory section ends
    where the next bank's heading begins, even though the theory worker
    won't extract from the bank itself.
    """
    entries: list[_DocOrderEntry] = []

    def visit_section(s: SchemaSection):
        if s.page_start is not None and s.title:
            entries.append(_DocOrderEntry(
                id=s.id,
                title=s.title,
                page_start=s.page_start,
                kind="section",
            ))
        for child in s.subsections or []:
            visit_section(child)

    for top in schema.sections or []:
        visit_section(top)

    def visit_excluded(ex: ExcludedSection):
        if ex.page_start is not None and ex.title:
            entries.append(_DocOrderEntry(
                id=None,
                title=ex.title,
                page_start=ex.page_start,
                kind="excluded",
            ))
        for sub in ex.subsections or []:
            visit_excluded(sub)

    for ex in schema.excluded_sections or []:
        visit_excluded(ex)

    # Stable sort by page_start. Same-page entries preserve visit order.
    entries.sort(key=lambda e: e.page_start)
    return entries


def _find_chapter_wrapper(
    section: SchemaSection, schema: BookSchema
) -> Optional[SchemaSection]:
    """Return the chapter wrapper (type='chapter') containing this section.

    If `section` IS itself the chapter wrapper, returns it.
    Returns None if no chapter wrapper found (defensive — every schema
    should have at least one chapter wrapper).
    """

    def contains(parent: SchemaSection, target_id: str) -> bool:
        if parent.id == target_id:
            return True
        for child in parent.subsections or []:
            if contains(child, target_id):
                return True
        return False

    for top in schema.sections or []:
        if top.type == "chapter" and contains(top, section.id):
            return top
    return None


# ─── Public API ──────────────────────────────────────────────────────────


def compute_extraction_slice(
    section: SchemaSection,
    schema: BookSchema,
    pdf_total_pages: int,
) -> SliceSpec:
    """Deterministically compute the page-range slice for a theory section.

    See module docstring for the full rules. Raises ``SliceComputationError``
    if the slice can't be resolved without faking page numbers — the caller
    should mark the section status='failed' and continue to the next.
    """
    if pdf_total_pages < 1:
        raise SliceComputationError(
            section.id, f"pdf_total_pages must be >= 1, got {pdf_total_pages}"
        )

    diagnostics: list[str] = []
    doc_order = _flatten_document_order(schema)

    # Locate this section in document order. Sections have unique ids per
    # schema validator rule §13.3. Defensive: -1 if not found.
    my_idx = -1
    for i, e in enumerate(doc_order):
        if e.id == section.id:
            my_idx = i
            break

    # ── page_start ────────────────────────────────────────────────────
    page_start: Optional[int] = section.page_start
    if page_start is None:
        if my_idx > 0:
            prev = doc_order[my_idx - 1]
            page_start = prev.page_start
            diagnostics.append(
                f"page_start inferred from previous section "
                f"'{prev.title}' on page {prev.page_start}"
            )
        else:
            raise SliceComputationError(
                section.id,
                "page_start missing from schema and no previous section "
                "to infer from (this is a schema bug — page_start is a mandate)",
            )

    if page_start < 1 or page_start > pdf_total_pages:
        raise SliceComputationError(
            section.id,
            f"page_start={page_start} out of bounds [1, {pdf_total_pages}]",
        )

    # ── is_container ──────────────────────────────────────────────────
    is_container = bool(section.subsections)

    # ── Schema-resilience for wrappers (don't trust bad page ranges) ──
    # Some schemas emit a wrapper with page_start > its first child's
    # page_start (e.g. "Chapter 4" pages 6-6 but children start on page 1).
    # Following the schema blindly would either (a) try to slice
    # backwards → SliceComputationError → wrapper marked failed → no
    # chapter intro content shown, or (b) bleed the wrong page into the
    # wrapper. Use the children's MIN page_start as the wrapper's start,
    # so the wrapper extracts only the genuine intro/heading before its
    # first child. Follows document sequence, not schema page math.
    if is_container:
        child_min: Optional[int] = None
        for child in section.subsections or []:
            if child.page_start is not None:
                if child_min is None or child.page_start < child_min:
                    child_min = child.page_start
        if child_min is not None and child_min < page_start:
            diagnostics.append(
                f"wrapper page_start corrected {page_start} → {child_min} "
                f"(schema had wrapper after its first child)"
            )
            page_start = child_min

    # ── next stop boundary ────────────────────────────────────────────
    # Container: its first child is the natural next stop.
    # Leaf:      the next entry in document order (which may be a Cat A
    #            section, excluded bank, or excluded sub-bank — all act as
    #            stop boundaries even though theory worker doesn't extract them).
    next_entry: Optional[_DocOrderEntry] = None

    if is_container:
        for child in section.subsections or []:
            if child.page_start is not None and child.title:
                next_entry = _DocOrderEntry(
                    id=child.id,
                    title=child.title,
                    page_start=child.page_start,
                    kind="section",
                )
                break

    if next_entry is None and my_idx >= 0:
        for j in range(my_idx + 1, len(doc_order)):
            e = doc_order[j]
            if e.page_start >= page_start:
                next_entry = e
                break

    # ── page_end ──────────────────────────────────────────────────────
    if next_entry is not None:
        # Container and leaf both stop where next entry begins. STOP anchor
        # tells Gemini not to extract next-section content from the shared
        # boundary page (see Q4 decision: strict, no content overlap).
        page_end = next_entry.page_start
        if section.page_end is not None and section.page_end != page_end:
            diagnostics.append(
                f"page_end overridden from schema ({section.page_end}) to "
                f"next-entry boundary ({page_end}) at "
                f"'{next_entry.title}'"
            )
    else:
        # Last section in document order. Try chapter wrapper's page_end.
        wrapper = _find_chapter_wrapper(section, schema)
        if wrapper is not None and wrapper.page_end is not None:
            page_end = wrapper.page_end
            diagnostics.append(
                f"page_end inferred from chapter wrapper "
                f"'{wrapper.title}' on page {wrapper.page_end}"
            )
        elif section.page_end is not None:
            page_end = section.page_end
            diagnostics.append(
                "page_end from section.page_end (last section, no wrapper to defer to)"
            )
        else:
            raise SliceComputationError(
                section.id,
                "page_end missing and no next section / chapter wrapper "
                "to infer from",
            )

    # Clamp page_end to PDF bounds (defensive — schema may overstate).
    if page_end > pdf_total_pages:
        diagnostics.append(
            f"page_end ({page_end}) clamped to pdf_total_pages ({pdf_total_pages})"
        )
        page_end = pdf_total_pages

    if page_end < page_start:
        raise SliceComputationError(
            section.id,
            f"computed page_end={page_end} < page_start={page_start} "
            f"(schema inconsistency)",
        )

    # ── STOP anchor ───────────────────────────────────────────────────
    stop_anchor_title: Optional[str] = None
    stop_anchor_page: Optional[int] = None
    if next_entry is not None:
        stop_anchor_title = next_entry.title
        stop_anchor_page = next_entry.page_start

    return SliceSpec(
        section_id=section.id,
        page_start=page_start,
        page_end=page_end,
        is_container=is_container,
        stop_anchor_title=stop_anchor_title,
        stop_anchor_page=stop_anchor_page,
        diagnostics=tuple(diagnostics),
    )
