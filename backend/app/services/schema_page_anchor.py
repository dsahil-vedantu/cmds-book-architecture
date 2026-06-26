"""Deterministic page-range anchor for schema sections.

Replaces Gemini-emitted page ranges (which hallucinate freely — see the
Integrals book where every section got page_start=page_end=6) with
pypdf-grounded values computed deterministically from the PDF text.

Algorithm (pure Python, no LLM):
    1. Walk every schema section in document order — main tree first
       (depth-first, parents before children), then excluded sections.
    2. For each section, locate its title in pypdf-extracted page text.
       Search is bounded by the previously-anchored section's page so
       repeated headings (e.g. "Introduction" appearing in every chapter)
       can't pull a later section's anchor backward.
    3. page_start = first hit. page_end = (next-anchored-section.page_start - 1)
       for leaves, or aggregated from children for parents.
    4. Validate: page_start ≤ page_end, monotonic within each parent's
       children, no sibling overlap. Report anomalies but never silently
       corrupt — sections whose title can't be located keep Gemini's value
       (best available) and get surfaced in the report.

This pass is INTENDED to run BEFORE the validator in schema_builder, so
the validator sees real pages and downstream consumers (theory slice,
question worker, figure embedder) operate on grounded ranges.

Failure modes (HONEST):
    - Scanned PDF → pypdf returns no text → pass skipped (returns unchanged
      schema + skipped_no_text=True). Validator + Gemini ranges still hold.
    - Title genuinely doesn't appear in PDF text → kept at Gemini's value,
      reported as "phantom" (caller may surface as warning).
    - Title appears multiple times → forward-search-bounded resolution
      picks the first occurrence at-or-after the previous section's page.
      This DOES NOT silently pick the wrong one — order is grounded by
      document flow, which matches the schema's declared order.

The single non-LLM source of truth for "what page does section X start on."
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.schemas.analyser import BookSchema, SchemaSection
from app.services.schema_postpass import (
    _extract_per_page_text,
    _normalize_for_match,
    _title_in_text,
)

logger = logging.getLogger(__name__)


@dataclass
class AnchorReport:
    """Diagnostic record of what the anchor pass did. Caller logs/surfaces."""
    skipped_no_text: bool = False
    """True if pypdf returned no text (scanned PDF). Schema returned unchanged."""

    total_sections: int = 0
    """Total schema sections (main + excluded) walked."""

    anchored: int = 0
    """Sections whose page_start was successfully relocated from PDF text."""

    confirmed: int = 0
    """Sections whose Gemini page_start already matched a PDF-located page."""

    repaired: int = 0
    """Sections whose page_start was MOVED from Gemini's value to a PDF hit."""

    phantoms: list[str] = field(default_factory=list)
    """Section titles that couldn't be found anywhere in the PDF text.
    Their pages are preserved at Gemini's values — caller decides whether
    to surface as schema warnings."""

    page_changes: list[dict[str, Any]] = field(default_factory=list)
    """Per-section diffs: {section_id, title, old_start, old_end, new_start, new_end}.
    Useful for logging the diff or downstream auditing."""

    sibling_overlap_warnings: list[str] = field(default_factory=list)
    """Pairs of sibling sections whose computed ranges overlap. Anchor
    pass does NOT silently rewrite to fix overlap — surfaces them so the
    validator (or human) can decide."""


def _doc_order_flatten(schema: BookSchema) -> list[SchemaSection]:
    """Depth-first walk of the entire schema (main tree + excluded).

    Parents come before children. Excluded sections come after the main
    tree, each excluded parent followed by its children. This ordering
    reflects how the PDF actually reads — chapter content first, then
    end-of-chapter exercise blocks.
    """
    out: list[SchemaSection] = []

    def walk(nodes: list[SchemaSection] | None) -> None:
        for n in nodes or []:
            out.append(n)
            walk(n.subsections)

    walk(schema.sections)
    walk(schema.excluded_sections)
    return out


def _candidate_needles(title_norm: str) -> list[str]:
    """Build a fallback chain of progressively-shorter needles.

    Long titles like "4.1 Fundamental theorem of integral calculus and
    Evaluation of definite integrals by substitution" rarely appear
    VERBATIM in PDF text — books print them wrapped, hyphenated, or
    split between heading line and subtitle. We try:
      1. Full normalized title          (strictest, fewest false positives)
      2. First 8 normalized tokens      (catches wrapped continuations)
      3. First 5 normalized tokens      (catches "Section 4.1 Fundamental theorem...")
      4. First 3 normalized tokens      (catches "4.1 Fundamental theorem")
    Returns ordered list of unique needles — caller picks the FIRST that
    finds a hit, so order matters (longest first = least false-positive).
    """
    if not title_norm:
        return []
    parts = title_norm.split()
    candidates = [title_norm]
    for n in (8, 5, 3):
        if len(parts) > n:
            candidates.append(" ".join(parts[:n]))
    # dedupe while preserving order
    seen, out = set(), []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _find_title_first_at_or_after(
    title_norm: str,
    per_page_norm: dict[int, str],
    min_page: int,
) -> int | None:
    """First page >= min_page where the title (or a prefix needle) appears.

    Tries the full title, then progressively shorter prefixes (see
    `_candidate_needles`). The FIRST needle to find a hit wins — so
    cross-line wrapped titles still resolve while strict substring
    correctness is preserved for short, exact-match titles.
    """
    needles = _candidate_needles(title_norm)
    pages = sorted(per_page_norm.keys())
    for needle in needles:
        for p in pages:
            if p < min_page:
                continue
            if _title_in_text(needle, per_page_norm[p]):
                return p
    return None


def _find_title_anywhere(
    title_norm: str,
    per_page_norm: dict[int, str],
) -> int | None:
    """First page anywhere in the document where title (or a prefix needle)
    appears. Same prefix-fallback chain as the bounded search.

    Used as last-resort salvage when the bounded search returns None —
    typically when sections come back out of order (e.g. schema mis-ordered
    or the title is referenced before its heading appears).
    """
    needles = _candidate_needles(title_norm)
    pages = sorted(per_page_norm.keys())
    for needle in needles:
        for p in pages:
            if _title_in_text(needle, per_page_norm[p]):
                return p
    return None


_FIG_CAPTION_RE = re.compile(
    r"^\s*(figure|fig|table|diagram|illustration|photo|graph|chart|plate)\.?\s*\d",
    re.IGNORECASE,
)

_NUM_TOKEN_RE = re.compile(r"^\d+(\.\d+)*$")


def _furniture_key(norm_line: str) -> str:
    """Normalized line with leading/trailing pure-number tokens stripped.

    Running headers are frequently glued by pypdf to a per-page figure or
    page number — "6.8 Chapter 6" on page 2, "6.10 Chapter 6" on page 4.
    Each raw line is then UNIQUE (different number), so exact-line repetition
    misses the header. Stripping the bounding number tokens collapses them
    all to one stable key ("chapter") so the repeated header is detected.
    """
    toks = norm_line.split()
    while toks and _NUM_TOKEN_RE.match(toks[0]):
        toks.pop(0)
    while toks and _NUM_TOKEN_RE.match(toks[-1]):
        toks.pop()
    return " ".join(toks)


def _positional_furniture_keys(pdf_bytes: bytes) -> set[str]:
    """Identify running-header/footer text by GEOMETRIC repetition.

    A running header/footer is the same short text printed at the SAME
    position on many pages. We detect it from pymupdf line bounding boxes:
    a number-stripped key (see `_furniture_key`) that appears on >= 3 pages
    AND whose vertical position is tightly clustered (it prints in the same
    horizontal band every time) is furniture — REGARDLESS of where that
    band sits (top, bottom, or mid-page) and REGARDLESS of multi-column
    text-stream order, because position comes from the box, not the stream.

    A genuine recurring heading ("Definition", "Illustration", "Exercise")
    appears at DIFFERENT vertical positions page to page, so its spread is
    wide and it is never flagged. This is what keeps real headings intact.

    Returns the set of furniture keys; `_strip_page_furniture` removes
    matching lines from the (pypdf) match text. On any pymupdf failure
    returns an empty set — the anchor then simply skips header stripping
    (degrades safely, never corrupts).
    """
    try:
        import fitz  # pymupdf
    except Exception as e:  # pragma: no cover - dependency present in prod
        logger.warning("pymupdf unavailable; skipping positional furniture: %s", e)
        return set()

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        logger.warning("pymupdf could not open PDF for furniture detection: %s", e)
        return set()

    key_pages: dict[str, set[int]] = {}
    key_ys: dict[str, list[float]] = {}
    try:
        for pno in range(doc.page_count):
            page = doc[pno]
            height = page.rect.height or 1.0
            try:
                data = page.get_text("dict")
            except Exception:
                continue
            for block in data.get("blocks", []):
                for line in block.get("lines", []):
                    text = "".join(s.get("text", "") for s in line.get("spans", []))
                    norm = _normalize_for_match(text)
                    # Cap length: headers/footers are short; never treat a
                    # full sentence/paragraph as furniture even if repeated.
                    if not norm or len(norm.split()) > 12:
                        continue
                    key = _furniture_key(norm)
                    if not key:
                        continue
                    y0 = (line.get("bbox") or [0, 0, 0, 0])[1]
                    key_pages.setdefault(key, set()).add(pno + 1)
                    key_ys.setdefault(key, []).append(y0 / height)
    finally:
        doc.close()

    furniture: set[str] = set()
    for key, pages in key_pages.items():
        if len(pages) < 3:
            continue
        ys = key_ys.get(key, [])
        # Tightly clustered vertical position across pages → running
        # header/footer. Wide spread → recurring mid-page heading → keep.
        if ys and (max(ys) - min(ys)) < 0.08:
            furniture.add(key)
    return furniture


def _strip_page_furniture(
    per_page: dict[int, str],
    furniture_keys: set[str],
) -> dict[int, str]:
    """Drop running-header/footer and figure-caption lines from each page's
    (pypdf) text BEFORE title matching, so a section title can only anchor
    to genuine heading/body text — never to page furniture or a caption.

    `furniture_keys` come from `_positional_furniture_keys` (geometry-based,
    so they are confirmed running headers/footers). Because that gate is
    positional, stripping a furniture key wherever it appears is safe — a
    real heading like "Definition" is never in the set. When nothing genuine
    remains for a title, the caller's phantom path keeps Gemini's page value.

      • Running headers / footers — collide with a chapter-root title, anchor
        it to the first page the header prints on, and drag the forward
        cursor so every later section is mis-placed too (the Trachea→p2 /
        Lungs→p2 regression).
      • Figure captions / diagram labels — "Figure 6.8 Human Respiratory
        System", "Fig. 6.7 ..." — would anchor a matching title to the
        figure's page instead of its heading.
    """
    cleaned: dict[int, str] = {}
    for p, text in per_page.items():
        kept: list[str] = []
        for ln in (text or "").splitlines():
            norm = _normalize_for_match(ln)
            if norm and furniture_keys and _furniture_key(norm) in furniture_keys:
                continue
            if _FIG_CAPTION_RE.match(ln or ""):
                continue
            kept.append(ln)
        cleaned[p] = "\n".join(kept)
    return cleaned


def anchor_pages_from_pdf(
    schema: BookSchema,
    pdf_bytes: bytes,
) -> tuple[BookSchema, AnchorReport]:
    """Replace schema's page ranges with PDF-grounded values.

    Returns (new_schema, report). The original schema is mutated in-place
    AND returned for caller ergonomics. Caller should log the report's
    `phantoms` and `sibling_overlap_warnings` so QA can review.

    Idempotent: running this pass twice on the same (schema, pdf) produces
    identical output. Safe to call multiple times.
    """
    report = AnchorReport()

    per_page = _extract_per_page_text(pdf_bytes)
    # Scanned PDFs return a per_page dict full of empty strings, not an
    # empty dict — so `if not per_page` doesn't catch them. We need the
    # aggregate text length to decide we have something to match against.
    # Threshold of 100 chars is a defensive floor: anything below that
    # almost certainly means pypdf failed (scanned / image-only PDF) and
    # any "match" would be spurious.
    total_text = sum(len(t) for t in per_page.values())
    if not per_page or total_text < 100:
        logger.info(
            "schema_page_anchor: pypdf returned %d total chars across %d pages — "
            "treating as scanned/image PDF and skipping. Gemini page ranges "
            "preserved as-is.",
            total_text, len(per_page),
        )
        report.skipped_no_text = True
        return schema, report

    # Strip running headers/footers and figure captions BEFORE normalizing —
    # otherwise a section title can anchor to page furniture (e.g. a "Chapter
    # 6" running header) or a figure caption, overwrite its correct page, and
    # drag the forward cursor so every later section is mis-placed too.
    # Furniture is detected by GEOMETRY (pymupdf boxes) so a header is caught
    # at any position and through multi-column reordering; matching stays on
    # the pypdf text to preserve verified anchor behavior.
    furniture_keys = _positional_furniture_keys(pdf_bytes)
    per_page_clean = _strip_page_furniture(per_page, furniture_keys)
    per_page_norm = {p: _normalize_for_match(t) for p, t in per_page_clean.items()}
    total_pages = max(per_page_norm.keys()) if per_page_norm else 0
    if total_pages <= 0:
        report.skipped_no_text = True
        return schema, report

    flat = _doc_order_flatten(schema)
    report.total_sections = len(flat)

    # ── Phase 1: anchor every section's page_start via forward-bounded
    # search. Track per-section new starts in a dict, indexed by Python
    # id() so we keep ordering even when section_id (slug) collides or
    # is missing.
    new_starts: dict[int, int | None] = {}  # id(section) → new page_start
    cursor = 1  # forward search lower bound

    for sec in flat:
        title_norm = _normalize_for_match(sec.title)
        if not title_norm:
            new_starts[id(sec)] = sec.page_start
            continue

        hit = _find_title_first_at_or_after(title_norm, per_page_norm, cursor)
        if hit is None:
            # Bounded search failed — try whole document
            hit = _find_title_anywhere(title_norm, per_page_norm)

        if hit is None:
            # Genuinely missing from PDF text (could be scanned page, OCR
            # gap, or Gemini hallucination). Preserve Gemini's value but
            # surface as phantom for caller to log.
            report.phantoms.append(sec.title or sec.id or "?")
            new_starts[id(sec)] = sec.page_start
            continue

        new_starts[id(sec)] = hit
        report.anchored += 1
        if sec.page_start == hit:
            report.confirmed += 1
        else:
            report.repaired += 1

        # Advance cursor so later sections can't anchor before this one.
        # NB: we DON'T advance past hit so a same-page subsection can also
        # anchor at this page (chapter heading + first subsection on one
        # page is legitimate).
        cursor = max(cursor, hit)

    # ── Phase 2: compute page_end for each section from the NEXT
    # section's start (in doc order). The last section's page_end is
    # total_pages. Parents are aggregated bottom-up after this pass.
    new_ends: dict[int, int] = {}
    for i, sec in enumerate(flat):
        my_start = new_starts.get(id(sec))
        if my_start is None:
            new_ends[id(sec)] = sec.page_end or total_pages
            continue
        # Find the next section whose start is STRICTLY AFTER mine
        # (siblings/same-page sections share the start day; the page_end
        # belongs to the section that owns the rest of the page).
        next_start: int | None = None
        for j in range(i + 1, len(flat)):
            cand = new_starts.get(id(flat[j]))
            if cand is not None and cand > my_start:
                next_start = cand
                break
        if next_start is None:
            new_ends[id(sec)] = total_pages
        else:
            new_ends[id(sec)] = max(my_start, next_start - 1)

    # ── Phase 3: parents must span all descendants. Walk bottom-up.
    def aggregate_parent(node: SchemaSection) -> None:
        for child in node.subsections or []:
            aggregate_parent(child)
        if not node.subsections:
            return
        child_starts = [
            new_starts.get(id(c)) for c in node.subsections
            if new_starts.get(id(c)) is not None
        ]
        child_ends = [
            new_ends.get(id(c)) for c in node.subsections
            if new_ends.get(id(c)) is not None
        ]
        if child_starts:
            new_starts[id(node)] = min(
                [new_starts.get(id(node)) or child_starts[0]] + child_starts
            )
        if child_ends:
            new_ends[id(node)] = max(
                [new_ends.get(id(node)) or child_ends[0]] + child_ends
            )

    for top in (schema.sections or []):
        aggregate_parent(top)
    for top in (schema.excluded_sections or []):
        aggregate_parent(top)

    # ── Phase 4: write back to the schema (mutates in place). Record
    # diff in report for auditing.
    for sec in flat:
        ns = new_starts.get(id(sec))
        ne = new_ends.get(id(sec))
        old_s, old_e = sec.page_start, sec.page_end
        if ns is not None and ns != old_s:
            sec.page_start = ns
        if ne is not None and ne != old_e:
            sec.page_end = ne
        if (sec.page_start != old_s) or (sec.page_end != old_e):
            # ExcludedSection has no .id field; SchemaSection does. Use
            # getattr so both shapes flow through the same diagnostic.
            report.page_changes.append({
                "section_id": getattr(sec, "id", None),
                "title": sec.title,
                "old_start": old_s,
                "old_end": old_e,
                "new_start": sec.page_start,
                "new_end": sec.page_end,
            })

    # ── Phase 5: sibling-overlap detection (non-mutating diagnostic). A
    # parent's children should have monotonic, non-overlapping ranges.
    def check_overlap(parent_label: str, children: list) -> None:
        # children may be a mix of SchemaSection / ExcludedSection — both
        # expose .title, .page_start, .page_end, .subsections, so this is
        # safe via duck typing. Only .id differs (Excluded has none).
        prev = None
        for c in children:
            if prev is not None:
                prev_end = prev.page_end if prev.page_end is not None else -1
                c_start = c.page_start if c.page_start is not None else 10**9
                if c_start <= prev_end:
                    report.sibling_overlap_warnings.append(
                        f"{parent_label}: '{prev.title}' page_end={prev_end} "
                        f"overlaps '{c.title}' page_start={c_start}"
                    )
            prev = c
            check_overlap(
                c.title or getattr(c, "id", None) or "?",
                c.subsections or [],
            )

    check_overlap("<root>", schema.sections or [])
    check_overlap("<excluded-root>", schema.excluded_sections or [])

    logger.info(
        "schema_page_anchor: walked=%d anchored=%d confirmed=%d repaired=%d "
        "phantoms=%d overlap_warnings=%d page_changes=%d",
        report.total_sections,
        report.anchored,
        report.confirmed,
        report.repaired,
        len(report.phantoms),
        len(report.sibling_overlap_warnings),
        len(report.page_changes),
    )

    return schema, report


__all__ = ["anchor_pages_from_pdf", "AnchorReport"]
