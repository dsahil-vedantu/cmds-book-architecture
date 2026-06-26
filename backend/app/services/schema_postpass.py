"""Deterministic schema post-pass — VERIFIER ONLY (no injection).

The analyser (Gemini) is the single source of truth for the schema. This
post-pass does NOT mutate the schema. It scans the PDF text via pypdf for
printed example/illustration/exercise markers and CROSS-CHECKS them against
what Gemini produced. Mismatches are returned as warnings only; the schema
is returned unchanged.

Why verifier-not-injector:
- pypdf text extraction is unreliable on scanned/image PDFs and on complex
  layouts (multi-column, sidebars, boxed labels) — regex on that garbage
  used to inject phantom nodes that overrode Gemini's correct OCR.
- The new strict OCR-ONLY Pass 3.5 in schema_gemini.txt makes the
  analyser's output authoritative; postpass injection became actively
  harmful (e.g., relabeling "Illustration 1" as "EXAMPLE 1").
- A QC report is more useful than silent mutation — the reviewer sees
  what Gemini might have missed and can act, instead of trusting a
  phantom-prone auto-fix.

Public entry point: ``verify_schema_against_pdf_text``.
A backward-compat shim ``enrich_schema_with_question_markers`` is preserved
so callers that haven't migrated still work — it now just calls the verifier
and returns the schema unchanged.
"""

from __future__ import annotations

import io
import logging
import re

from app.schemas.analyser import BookSchema, SchemaSection

logger = logging.getLogger(__name__)

# Printed question/example/exercise markers. Case-insensitive; anchored to
# start of a trimmed line so we don't match "see example 9.1" mid-sentence.
# Each pattern captures (kind_keyword, number_if_any).
# Numbered patterns are listed first (more specific match wins).
_LABEL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Worked Example", re.compile(r"^\s*(WORKED\s+EXAMPLE)\s+(\d+(?:\.\d+)?)\b", re.IGNORECASE)),
    ("Solved Example", re.compile(r"^\s*(SOLVED\s+EXAMPLE)\s+(\d+(?:\.\d+)?)\b", re.IGNORECASE)),
    ("Practice Problem", re.compile(r"^\s*(PRACTICE\s+PROBLEM)\s+(\d+(?:\.\d+)?)\b", re.IGNORECASE)),
    ("Example", re.compile(r"^\s*(EXAMPLE)\s+(\d+(?:\.\d+)?)\b", re.IGNORECASE)),
    ("Illustration", re.compile(r"^\s*(ILLUSTRATION)\s+(\d+(?:\.\d+)?)\b", re.IGNORECASE)),
    ("Exercise", re.compile(r"^\s*(EXERCISE)\s+(\d+(?:\.\d+)?)\b", re.IGNORECASE)),
    ("Problem", re.compile(r"^\s*(PROBLEM)\s+(\d+(?:\.\d+)?)\b", re.IGNORECASE)),
    ("Activity", re.compile(r"^\s*(ACTIVITY)\s+(\d+(?:\.\d+)?)\b", re.IGNORECASE)),
    ("Try It", re.compile(r"^\s*(TRY\s+IT)\s*(\d+(?:\.\d+)?)?\b", re.IGNORECASE)),
    ("Progress Check", re.compile(r"^\s*(PROGRESS\s+CHECK)\b()", re.IGNORECASE)),
    ("Quick Check", re.compile(r"^\s*(QUICK\s+CHECK)\b()", re.IGNORECASE)),
]


# Backward-compat alias
_EXAMPLE_PATTERNS: list[re.Pattern[str]] = [pat for _, pat in _LABEL_PATTERNS]


def _extract_per_page_text(pdf_bytes: bytes) -> dict[int, str]:
    """Return {page_number_1_indexed: text} for the PDF.

    Uses pypdf since it's already a project dependency. Falls back to empty
    text on a per-page extraction failure rather than aborting the whole pass.
    """
    try:
        from pypdf import PdfReader
    except Exception as e:
        logger.warning("pypdf not available, skipping schema post-pass: %s", e)
        return {}

    out: dict[int, str] = {}
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        for i, page in enumerate(reader.pages, start=1):
            try:
                out[i] = page.extract_text() or ""
            except Exception as e:
                logger.debug("page %s text extract failed: %s", i, e)
                out[i] = ""
    except Exception as e:
        logger.warning("PDF read failed in post-pass: %s", e)
    return out


def _find_example_markers(per_page_text: dict[int, str]) -> list[tuple[str, int]]:
    """Backward-compat: return list of (example_number, page) tuples.

    Kept for any external callers; new code should use ``_find_labels``.
    """
    return [(num, page) for kind, num, page in _find_labels(per_page_text)
            if kind in ("Example", "Worked Example", "Solved Example", "Illustration")]


def _find_labels(per_page_text: dict[int, str]) -> list[tuple[str, str, int]]:
    """Return list of (kind, number, page) tuples for every printed label
    found anywhere in the PDF.

    - ``kind`` is the canonical printed keyword: "Example", "Illustration",
      "Exercise", "Problem", "Activity", "Try It", "Progress Check", etc.
    - ``number`` is the printed number if any, else "" (e.g., "Progress Check"
      may be unnumbered).
    - ``page`` is the 1-indexed page where the label first appears.

    Deduplicates by (kind, number) — first-seen page wins.
    """
    seen: dict[tuple[str, str], int] = {}
    for page, text in per_page_text.items():
        if not text:
            continue
        for line in text.splitlines():
            for kind, pat in _LABEL_PATTERNS:
                m = pat.match(line)
                if m:
                    num = (m.group(2) or "").strip() if m.lastindex and m.lastindex >= 2 else ""
                    key = (kind, num)
                    if key not in seen:
                        seen[key] = page
                    break  # one match per line
    return sorted([(k[0], k[1], p) for k, p in seen.items()],
                  key=lambda t: (t[2], t[0], t[1]))


def _walk(sections: list[SchemaSection]):
    """Depth-first walker yielding (parent_list, node)."""
    for s in sections:
        yield sections, s
        yield from _walk(s.subsections)


def _find_parent(
    schema: BookSchema, page: int
) -> tuple[list[SchemaSection], SchemaSection] | None:
    """Pick the deepest schema node whose page range contains ``page``.

    Returns the (siblings_list, node) pair so the caller can append a new
    child to the right place. Falls back to the closest section by page_start
    if no node contains the page exactly.
    """
    best: tuple[list[SchemaSection], SchemaSection, int] | None = None  # (siblings, node, depth)

    def _walk_with_depth(siblings: list[SchemaSection], depth: int) -> None:
        nonlocal best
        for s in siblings:
            ps, pe = s.page_start, s.page_end
            if ps is not None and pe is not None and ps <= page <= pe:
                if best is None or depth > best[2]:
                    best = (siblings, s, depth)
            _walk_with_depth(s.subsections, depth + 1)

    _walk_with_depth(schema.sections, 0)
    if best is not None:
        return best[0], best[1]

    # Fallback — closest section whose page_start <= page
    closest: tuple[list[SchemaSection], SchemaSection, int] | None = None
    for siblings, node in _walk(schema.sections):
        if node.page_start is not None and node.page_start <= page:
            if closest is None or node.page_start > closest[2]:
                closest = (siblings, node, node.page_start)
    if closest is not None:
        return closest[0], closest[1]
    return None


def _example_already_present(parent: SchemaSection, example_num: str) -> bool:
    """True if any descendant of ``parent`` already represents this example."""
    needle = f"example-{example_num}".lower()
    title_needle = f"example {example_num}".lower()

    def _check(node: SchemaSection) -> bool:
        if needle in (node.id or "").lower():
            return True
        if title_needle in (node.title or "").lower():
            return True
        return any(_check(c) for c in node.subsections)

    return any(_check(c) for c in parent.subsections)


def _label_present_in_schema(schema: BookSchema, kind: str, num: str) -> bool:
    """True if any node in the schema represents this (kind, num) label.

    Matches by title (case-insensitive substring on canonical kind+num) OR by
    id (kind+num appears anywhere in the id). Tolerant of casing differences.
    """
    title_needle_a = (f"{kind} {num}").strip().lower()
    title_needle_b = (f"{kind}").strip().lower()  # for unnumbered (e.g. Progress Check)
    id_kind = kind.lower().replace(" ", "-")
    id_needle = f"{id_kind}-{num}".strip("-").lower()
    id_needle_b = id_kind.lower()

    def _check(node: SchemaSection) -> bool:
        title = (node.title or "").lower()
        node_id = (node.id or "").lower()
        if num:
            if title_needle_a in title or id_needle in node_id:
                return True
        else:
            if title_needle_b == title or id_needle_b in node_id:
                return True
        return any(_check(c) for c in node.subsections)

    return any(_check(s) for s in schema.sections)


def verify_schema_against_pdf_text(
    pdf_bytes: bytes, schema: BookSchema
) -> tuple[BookSchema, list[str]]:
    """Cross-check schema labeled items against pypdf-extracted PDF text.

    Returns ``(schema_unchanged, warnings)``. The schema is NEVER mutated. If
    pypdf can't extract usable text (e.g., scanned PDF), returns an empty
    warnings list — Gemini Vision is the sole source of truth in that case.

    Warning format: human-readable strings indicating labels that pypdf saw
    in the PDF text but Gemini did not include in the schema. These are
    candidates for QC review, not bugs to fix automatically.
    """
    warnings: list[str] = []
    per_page = _extract_per_page_text(pdf_bytes)
    if not per_page:
        logger.info("schema verifier: pypdf returned no text (likely scanned PDF) — skipping cross-check")
        return schema, warnings

    labels = _find_labels(per_page)
    if not labels:
        logger.info("schema verifier: no printed labels detected in PDF text")
        return schema, warnings

    missing: list[tuple[str, str, int]] = []
    for kind, num, page in labels:
        if not _label_present_in_schema(schema, kind, num):
            missing.append((kind, num, page))

    if missing:
        for kind, num, page in missing:
            display = f"{kind} {num}".strip()
            msg = f"pypdf saw '{display}' on page {page} but Gemini schema did not include it"
            warnings.append(msg)
            logger.info("schema verifier: %s", msg)
        logger.info(
            "schema verifier: %s candidate misses across %s detected labels",
            len(missing), len(labels),
        )
    else:
        logger.info(
            "schema verifier: all %s detected labels present in schema",
            len(labels),
        )

    return schema, warnings


# ─── SCHEMA Week 1 Day 5 — Cross-check: schema → PDF ──────────────
# Verifies each schema section's title actually appears on its claimed
# page_start. Catches the "EXAMPLE 9.18 → page=9" case where Gemini
# grabbed a label number as the page (page=9 is a valid integer the
# validator can't reject, but pypdf shows "EXAMPLE 9.18" actually
# appears on page 14).
#
# Auto-corrects when divergence detected (pypdf is ground truth).
# Surfaces phantom sections (title nowhere in PDF) as warnings.
# Skipped for scanned/image-only PDFs (no text layer = no ground truth).


from dataclasses import dataclass, field


@dataclass(frozen=True)
class PageCorrection:
    """Schema's claimed page differed from pypdf-found location."""

    section_id: str | None
    section_title: str
    claimed_page_start: int
    actual_page_start: int
    """page where title was actually found via pypdf"""


@dataclass(frozen=True)
class PhantomSection:
    """Schema claims a section that pypdf cannot find anywhere in the PDF."""

    section_id: str | None
    section_title: str
    claimed_page_start: int | None


@dataclass(frozen=True)
class CrossCheckResult:
    """Outcome of cross-checking schema sections against PDF text."""

    corrections: list[PageCorrection] = field(default_factory=list)
    """Sections whose page_start was auto-corrected."""

    phantoms: list[PhantomSection] = field(default_factory=list)
    """Sections whose title couldn't be found anywhere in PDF text."""

    confirmed: int = 0
    """Sections whose title was found at the claimed page (no change needed)."""

    skipped_no_text: bool = False
    """True if pypdf returned no text (scanned PDF). All checks skipped."""

    skipped_count: int = 0
    """Sections skipped from cross-check (e.g. top-level chapters with
    very broad page ranges where the title spans only the start)."""


def _normalize_for_match(s: str) -> str:
    """Lowercase, collapse whitespace, strip surrounding punctuation.

    Used to match section titles against extracted PDF text. Both sides
    go through the same normalizer so we compare apples-to-apples.
    """
    if not s:
        return ""
    return " ".join(s.lower().split()).strip()


def _title_in_text(title_norm: str, text_norm: str) -> bool:
    """True if the normalized title appears in the normalized page text.

    Uses substring match with word-boundary check on each side so we
    don't match "Carbon" inside "Carbon Compounds" as a false positive.

    For unnumbered short titles ("Introduction", "Summary"), still uses
    substring — the false-positive risk is mostly an issue when the
    title is genuinely common across the PDF, which we handle at the
    SEARCH step (multiple matches → undecidable → skip correction).
    """
    if not title_norm or not text_norm:
        return False
    idx = text_norm.find(title_norm)
    if idx < 0:
        return False
    # Check boundaries — chars at position idx-1 and idx+len(title_norm)
    # must NOT be alphanumeric (else we matched mid-word).
    before_ok = (idx == 0) or not text_norm[idx - 1].isalnum()
    after_idx = idx + len(title_norm)
    after_ok = (after_idx >= len(text_norm)) or not text_norm[after_idx].isalnum()
    return before_ok and after_ok


def _find_title_in_pdf(
    title_norm: str, per_page_text: dict[int, str]
) -> list[int]:
    """Return all 1-indexed pages where title appears (using same matcher
    as _title_in_text). Empty list if nowhere.
    """
    pages = []
    for page_num, text in per_page_text.items():
        text_norm = _normalize_for_match(text)
        if _title_in_text(title_norm, text_norm):
            pages.append(page_num)
    return sorted(pages)


def cross_check_section_pages(
    pdf_bytes: bytes,
    schema: BookSchema,
    *,
    skip_top_level: bool = True,
    page_tolerance: int = 1,
) -> CrossCheckResult:
    """Cross-check every section's claimed page_start against PDF text.

    For each section:
      1. If title appears in text at claimed_page_start (± tolerance) → OK
      2. If not, search whole PDF
         a. Found on exactly 1 page → CORRECTION (claimed → actual)
         b. Found on multiple pages → ambiguous, skip (claimed might still be correct)
         c. Not found anywhere → PHANTOM

    Args:
        pdf_bytes: PDF binary content
        schema: BookSchema to verify
        skip_top_level: If True, skip top-level chapter sections (their
            page ranges are usually too broad for meaningful per-page
            cross-check)
        page_tolerance: Pages around claimed_page_start to also check
            (titles sometimes wrap to next page).

    Returns CrossCheckResult. Caller decides whether to APPLY corrections
    or just record them in schema_warnings.

    Pure function — no DB I/O. Skipped for scanned PDFs (pypdf returns
    empty text).
    """
    result_corrections: list[PageCorrection] = []
    result_phantoms: list[PhantomSection] = []
    confirmed = 0
    skipped = 0

    per_page = _extract_per_page_text(pdf_bytes)
    if not per_page:
        logger.info(
            "schema cross-check: pypdf returned no text (scanned PDF) — skipping"
        )
        return CrossCheckResult(skipped_no_text=True)

    # Pre-normalize all page text once
    per_page_norm = {p: _normalize_for_match(t) for p, t in per_page.items()}

    def _walk(sections: list[SchemaSection], depth: int) -> None:
        nonlocal confirmed, skipped
        for section in sections:
            # Skip top-level chapters by default — their page ranges span
            # the whole chapter and the title appears only at the start,
            # which would match. But cross-check on subsections is more
            # informative.
            if skip_top_level and depth == 0:
                _walk(section.subsections, depth + 1)
                continue

            title = section.title or ""
            title_norm = _normalize_for_match(title)
            if not title_norm:
                # Empty title — can't cross-check
                skipped += 1
                _walk(section.subsections, depth + 1)
                continue

            ps = section.page_start
            if not isinstance(ps, int) or ps < 1:
                # No valid page_start — can't even attempt local check
                # (search whole PDF as fallback below)
                ps = None

            # Step 1: title on claimed page (± tolerance)?
            local_hit = False
            if ps is not None:
                check_pages = range(
                    max(1, ps - page_tolerance),
                    ps + page_tolerance + 1,
                )
                for cp in check_pages:
                    if cp in per_page_norm and _title_in_text(
                        title_norm, per_page_norm[cp]
                    ):
                        local_hit = True
                        break

            if local_hit:
                confirmed += 1
                _walk(section.subsections, depth + 1)
                continue

            # Step 2: not at claimed page — search whole PDF
            found_pages = _find_title_in_pdf(title_norm, per_page)
            if len(found_pages) == 1:
                # Deterministic correction
                actual = found_pages[0]
                # Only emit correction if it actually differs from claimed
                if ps != actual:
                    result_corrections.append(PageCorrection(
                        section_id=section.id,
                        section_title=title,
                        claimed_page_start=ps if ps is not None else -1,
                        actual_page_start=actual,
                    ))
                else:
                    confirmed += 1
            elif len(found_pages) > 1:
                # Ambiguous — title appears multiple times. Claimed may
                # still be correct if it's in found_pages; otherwise skip.
                if ps in found_pages:
                    confirmed += 1
                else:
                    skipped += 1
            else:
                # PHANTOM — title nowhere in PDF text
                result_phantoms.append(PhantomSection(
                    section_id=section.id,
                    section_title=title,
                    claimed_page_start=ps,
                ))

            _walk(section.subsections, depth + 1)

    _walk(schema.sections, 0)

    return CrossCheckResult(
        corrections=result_corrections,
        phantoms=result_phantoms,
        confirmed=confirmed,
        skipped_count=skipped,
    )


def apply_page_corrections(
    schema: BookSchema, corrections: list[PageCorrection]
) -> BookSchema:
    """Apply auto-corrections from cross-check to the schema.

    For each correction, find the section by id and update its
    page_start. Returns the (potentially mutated) schema.

    Only updates page_start; page_end is corrected by the separate
    cross_check_page_ends pass (Day 8 — pypdf-verified page_end).
    """
    by_id = {c.section_id: c for c in corrections if c.section_id}
    if not by_id:
        return schema

    def _walk(sections: list[SchemaSection]) -> None:
        for s in sections:
            c = by_id.get(s.id)
            if c is not None:
                old = s.page_start
                s.page_start = c.actual_page_start
                logger.info(
                    "schema cross-check: corrected '%s' page_start %s → %s",
                    c.section_title, old, c.actual_page_start,
                )
            _walk(s.subsections)

    _walk(schema.sections)
    return schema


# ─── DAY 8 — page_end cross-check (pypdf-verified) ─────────────────


@dataclass(frozen=True)
class PageEndCorrection:
    """Section's page_end claimed beyond where its successor actually starts."""

    section_id: str | None
    section_title: str
    claimed_page_end: int
    corrected_page_end: int
    """Computed from where the NEXT heading actually appears in PDF text.
    Shared-boundary aware: if next heading sits mid-page X, this section's
    content can run to page X (corrected = X). If next heading sits at top
    of page X, this section ends at page X-1 (corrected = X-1)."""
    next_heading_title: str
    """The heading used as the bound (next section in document order)."""


# Fraction of page text length within which a heading is considered
# "at top of page" (so the previous section ends at page-1, not page).
# Page text from pypdf often begins with a running header / page number,
# so 0.15 gives some grace for that prefix.
_TOP_OF_PAGE_THRESHOLD = 0.15


def _find_title_position_on_page(
    title_norm: str, page_text_norm: str
) -> int | None:
    """Return character position where title_norm first appears on page text,
    using the same word-boundary rule as _title_in_text. None if absent."""
    if not title_norm or not page_text_norm:
        return None
    idx = page_text_norm.find(title_norm)
    if idx < 0:
        return None
    before_ok = (idx == 0) or not page_text_norm[idx - 1].isalnum()
    after_idx = idx + len(title_norm)
    after_ok = (after_idx >= len(page_text_norm)) or not page_text_norm[after_idx].isalnum()
    return idx if (before_ok and after_ok) else None


def _next_bound_pairs(
    schema: BookSchema,
) -> list[tuple[SchemaSection, str]]:
    """For each section, compute its NEXT-NON-DESCENDANT in document order.

    A parent's page_end must cover all its descendants (per §9.4 child-
    within-parent rule), so a CHILD cannot bound a parent's page_end.
    The correct bound is the next section that is NOT a descendant of
    the section being checked — typically its next sibling, or an
    ancestor's next sibling, walking up the tree.

    Pre-order DFS assigns each section a contiguous index range in the
    flat list: [start, end_of_last_descendant]. The next-non-descendant
    is at index (end + 1).

    Returns: list of (section, next_heading_title) pairs. Sections whose
    next-non-descendant cannot be determined (very last section in tree
    with no excluded_sections fallback) are omitted.
    """
    flat: list[SchemaSection] = []
    end_index_of: dict[int, int] = {}

    def _walk(sections: list[SchemaSection]) -> None:
        for s in sections:
            flat.append(s)
            start_after = len(flat) - 1
            if s.subsections:
                _walk(s.subsections)
            end_index_of[id(s)] = len(flat) - 1
            _ = start_after  # only kept for clarity

    _walk(schema.sections)

    pairs: list[tuple[SchemaSection, str]] = []
    first_excluded_title = (
        (schema.excluded_sections[0].title or "").strip()
        if schema.excluded_sections else ""
    )

    for sec in flat:
        end_idx = end_index_of.get(id(sec))
        if end_idx is None:
            continue
        next_idx = end_idx + 1
        if next_idx < len(flat):
            next_title = (flat[next_idx].title or "").strip()
        else:
            # This section's subtree is the last in the sections tree —
            # fall back to first excluded section as bound (typical:
            # chapter ends, end-of-chapter banks follow).
            next_title = first_excluded_title
        if next_title:
            pairs.append((sec, next_title))

    return pairs


def cross_check_page_ends(
    pdf_bytes: bytes, schema: BookSchema
) -> list[PageEndCorrection]:
    """Verify every section's page_end against where the NEXT heading appears
    in PDF text. Auto-correct (narrow only) when claimed page_end exceeds
    the computed bound.

    For each section A in document order (depth-first, pre-order across the
    sections tree):
      1. Identify B = next heading in document order (next section in flat
         walk). If A is the last, also consider the first excluded_sections
         entry's title as a bound — the chapter's question banks usually
         follow theory and provide an end marker.
      2. Locate B.title in PDF text via pypdf. Skip A if B's title is not
         findable (phantom B / scanned PDF / duplicate match).
      3. Determine B's position WITHIN its page:
         - Position in first 15% of page text → B owns the top of that
           page → A.page_end_bound = B.actual_page_start - 1
         - Position later on the page → shared boundary (§9.2) → A may
           have content on the same page → A.page_end_bound = B.actual_page_start
      4. If A.page_end > A.page_end_bound → emit a PageEndCorrection
         (narrowing only — never extend).

    Skipped for scanned PDFs (pypdf returns empty text).
    Skipped for the very last section in document order (no successor to
    bound against).
    Skipped when A.page_end is already None / invalid (different rule
    catches that).

    Pure function — no DB I/O.
    """
    per_page = _extract_per_page_text(pdf_bytes)
    if not per_page:
        logger.info(
            "schema cross-check: pypdf returned no text — page_end check skipped"
        )
        return []

    per_page_norm = {p: _normalize_for_match(t) for p, t in per_page.items()}
    pairs = _next_bound_pairs(schema)
    if not pairs:
        return []

    out: list[PageEndCorrection] = []
    for sec, next_title in pairs:
        # Must have a valid integer page_end to bound
        pe = sec.page_end
        if not isinstance(pe, int) or pe < 1:
            continue

        title_norm = _normalize_for_match(next_title)
        if not title_norm:
            continue

        # Find all pages where next_title appears at or after this
        # section's page_start (the next heading can't logically appear
        # before this section began).
        ps_floor = sec.page_start if isinstance(sec.page_start, int) else 1
        candidate_pages: list[int] = []
        for p in sorted(per_page_norm):
            if p < ps_floor:
                continue
            if _title_in_text(title_norm, per_page_norm[p]):
                candidate_pages.append(p)

        if not candidate_pages:
            # Next heading not findable in PDF text — can't bound this
            # section's page_end (B may be a phantom or text was sliced
            # out). Leave alone.
            continue

        # When next_title appears multiple times (e.g. generic words like
        # "Activities", "Summary", "Solutions", or running-header
        # echoes), prefer the candidate CLOSEST to this section's claimed
        # page_end. That's our best bet for "the actual next heading"
        # rather than an in-section duplicate or running header.
        next_actual_page = min(
            candidate_pages,
            key=lambda p: abs(p - pe),
        )

        # Determine position of next_title within its page text
        page_text = per_page_norm[next_actual_page]
        pos = _find_title_position_on_page(title_norm, page_text) or 0
        position_fraction = (pos / max(1, len(page_text))) if page_text else 1.0

        if position_fraction <= _TOP_OF_PAGE_THRESHOLD:
            # Next heading sits at top of page → it owns the page →
            # this section ends one page earlier.
            bound = next_actual_page - 1
        else:
            # Shared boundary — both A's tail content and B's heading
            # share the same page. A can run to (but no further than)
            # next_actual_page.
            bound = next_actual_page

        # Only emit a correction when narrowing (never extend).
        # Skip degenerate cases: bound below this section's page_start
        # would invert the range — leave for validator/retry instead.
        if not (pe > bound and bound >= (sec.page_start or 1)):
            continue

        # Sanity guard: refuse over-aggressive corrections that would
        # shrink the section's claimed length by more than 50%.
        # Real-world cause: ambiguous generic title (e.g. "Activities")
        # appearing inside the section as an inline heading too — the
        # nearest-to-page_end heuristic above mitigates this, but as a
        # belt-and-braces check, require the correction to keep at
        # least half of the original claimed length.
        ps = sec.page_start if isinstance(sec.page_start, int) else 1
        original_length = max(1, pe - ps + 1)
        new_length = max(0, bound - ps + 1)
        if new_length * 2 < original_length:
            # Too drastic — most likely a wrong-match. Skip.
            logger.info(
                "schema page_end check: skipping risky correction for "
                "'%s' (claimed %d→%d via '%s' would cut from %d to %d pages)",
                sec.title, pe, bound, next_title, original_length, new_length,
            )
            continue

        out.append(PageEndCorrection(
            section_id=sec.id,
            section_title=(sec.title or ""),
            claimed_page_end=pe,
            corrected_page_end=bound,
            next_heading_title=next_title,
        ))

    return out


def apply_page_end_corrections(
    schema: BookSchema, corrections: list[PageEndCorrection]
) -> BookSchema:
    """Apply page_end corrections from cross_check_page_ends to schema.

    Find each section by id and narrow its page_end. Returns the
    (potentially mutated) schema. Never extends page_end — corrections
    are always narrowing per the function's guarantees.
    """
    by_id = {c.section_id: c for c in corrections if c.section_id}
    if not by_id:
        return schema

    def _walk(sections: list[SchemaSection]) -> None:
        for s in sections:
            c = by_id.get(s.id)
            if c is not None:
                old = s.page_end
                s.page_end = c.corrected_page_end
                logger.info(
                    "schema cross-check: corrected '%s' page_end %s → %s "
                    "(next heading '%s' starts page %s)",
                    c.section_title, old, c.corrected_page_end,
                    c.next_heading_title,
                    (c.corrected_page_end + 1) if old > c.corrected_page_end else c.corrected_page_end,
                )
            _walk(s.subsections)

    _walk(schema.sections)
    return schema


