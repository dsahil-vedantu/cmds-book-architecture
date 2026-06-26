"""Deterministic post-pass sanitizer for Cat A nesting rule.

Rule: every Cat A subsection (content_types includes "questions") MUST
nest under the theory (Cat B) section whose page range contains the Cat
A's OWN printed heading.

Two layers:

1. SIBLING layer (legacy, always applied):
   Walks each parent's children in array order. When a Cat A is found
   AFTER a Cat B sibling at the same level, move the Cat A to be a
   child of that Cat B. Catches the trivial case where Gemini emitted
   Cat A as a flat sibling of theory at the same depth.

2. POSITIONAL layer (new, applied when pdf_bytes provided):
   Uses pymupdf to find the PRINTED bbox of each section's own heading.
   Then for each Cat A, finds which theory (Cat B) section's page range
   ACTUALLY contains the Cat A's (page, y_top) anchor — preferring the
   deepest leaf theory section. On same-page ties, headings whose y_top
   is LESS than the Cat A's y_top precede it (and so are candidates).
   Re-parents the Cat A if its current parent disagrees.

   Positional layer falls back gracefully when:
     * PDF has no extractable text on a page (scanned PDF) → skip pass
     * A specific section's heading is not findable on its claimed
       page_start → use (page_start, 0) as a coarse anchor

Idempotent — running twice produces the same tree.

Public API:
    sanitize_cat_a_nesting(schema_dict, pdf_bytes=None) -> (schema, report)
    sanitize_cat_a_by_position(schema_dict, pdf_bytes) -> (schema, report)

The returned schema is a NEW dict; input is not mutated.
"""
from __future__ import annotations

import copy
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SanitizeReport:
    """Telemetry of a sanitize_cat_a_nesting() run."""
    reparented: int = 0
    moves: list[dict] = field(default_factory=list)
    positional_reparented: int = 0
    positional_skipped_no_text: bool = False

    def __bool__(self) -> bool:
        return self.reparented > 0 or self.positional_reparented > 0


def _is_cat_a(section: dict) -> bool:
    ct = section.get("content_types") or []
    return isinstance(ct, list) and "questions" in ct


def _is_cat_b(section: dict) -> bool:
    ct = section.get("content_types") or []
    return isinstance(ct, list) and "theory" in ct


def _sanitize_children(
    parent: dict,
    report: SanitizeReport,
) -> None:
    """Re-parent misplaced Cat A children under their preceding Cat B sibling.

    Walks `parent["subsections"]` in array order. When a Cat A is found
    AFTER a Cat B sibling at the same level, move the Cat A to be a
    child of that Cat B.

    Modifies `parent["subsections"]` in place.
    """
    children = parent.get("subsections")
    if not children:
        return

    # First pass: recursively sanitize each child's own subtree.
    for child in children:
        if isinstance(child, dict):
            _sanitize_children(child, report)

    # Second pass: re-parent at THIS level.
    new_children: list = []
    last_cat_b: dict | None = None
    for child in children:
        if not isinstance(child, dict):
            new_children.append(child)
            continue
        if _is_cat_b(child):
            last_cat_b = child
            new_children.append(child)
            continue
        if _is_cat_a(child) and last_cat_b is not None:
            # Move under last_cat_b
            last_cat_b.setdefault("subsections", []).append(child)
            report.reparented += 1
            report.moves.append({
                "cat_a_id": child.get("id"),
                "cat_a_title": child.get("title"),
                "moved_under_id": last_cat_b.get("id"),
                "moved_under_title": last_cat_b.get("title"),
                "from_parent_id": parent.get("id") or "ROOT",
                "layer": "sibling",
            })
            continue
        # Anything else — keep at this level (no Cat B precedes it,
        # OR it's a wrapper / mixed section type).
        new_children.append(child)

    parent["subsections"] = new_children


# ─── POSITIONAL LAYER ──────────────────────────────────────────────


def _normalize_text_for_search(s: str) -> str:
    """Lower-case + collapse whitespace; used to compare heading text
    against PDF-extracted blocks."""
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _find_heading_bbox_on_page(
    page, title: str
) -> tuple[float, float] | None:
    """Locate the bbox (x0, y0) of `title` on a pymupdf `page`.

    Strategy:
      1. Try `page.search_for(title)` — exact-ish match. Returns list of
         fitz.Rect.
      2. If empty, try a normalized search by walking `page.get_text("blocks")`
         and matching the first block whose normalized text *starts with*
         the normalized title (Gemini's heading text may have trailing
         dot/colon/page-number noise; the leading prefix is the stable part).

    Returns (page_y_top, x0) hash-stable on the y-coordinate; we only
    need y for the same-page tie-break. Returns None if not found.
    """
    if not title:
        return None
    # search_for is robust for short distinctive headings like "EXAMPLE 9.5"
    try:
        rects = page.search_for(title)
    except Exception:
        rects = []
    if rects:
        # Top-most occurrence
        r = min(rects, key=lambda rr: rr.y0)
        return (float(r.y0), float(r.x0))

    # Fallback: scan blocks for prefix match.
    norm_title = _normalize_text_for_search(title)
    if not norm_title:
        return None
    try:
        blocks = page.get_text("blocks") or []
    except Exception:
        blocks = []
    # blocks is list of (x0, y0, x1, y1, text, block_no, block_type)
    candidates = []
    for b in blocks:
        if len(b) < 5:
            continue
        x0, y0, _x1, _y1, text = b[0], b[1], b[2], b[3], b[4]
        norm = _normalize_text_for_search(text)
        if norm.startswith(norm_title) or norm_title in norm:
            candidates.append((float(y0), float(x0)))
    if candidates:
        return min(candidates)  # top-most
    return None


def _page_has_extractable_text(page) -> bool:
    """True if the page yields any text via get_text. Scanned PDFs return
    empty strings on every page."""
    try:
        txt = page.get_text("text") or ""
    except Exception:
        txt = ""
    return bool(txt.strip())


def _collect_theory_anchors(
    sections: list[dict],
    doc,
    *,
    pdf_total_pages: int,
) -> tuple[list[dict], bool]:
    """Walk the section tree and produce a flat list of theory anchors.

    Each anchor is:
      {
        "section": <ref to dict in tree>,
        "id": str,
        "title": str,
        "depth": int,         # 0 = top-level (deeper = more specific)
        "page_start": int,
        "page_end": int,      # may be inferred from page_start if missing
        "y_top": float,       # 0.0 if not found on PDF
        "is_leaf": bool,      # no theory descendants — most specific
      }

    Returns (anchors_in_doc_order, any_text_extracted).
    """
    out: list[dict] = []
    any_text = False

    def walk(node_list: list, depth: int):
        nonlocal any_text
        for s in node_list or []:
            if not isinstance(s, dict):
                continue
            ps = s.get("page_start")
            pe = s.get("page_end")
            if not isinstance(ps, int) or isinstance(ps, bool):
                # No usable page; recurse but skip anchoring.
                walk(s.get("subsections") or [], depth + 1)
                continue
            if not isinstance(pe, int) or isinstance(pe, bool):
                pe = ps  # collapse
            # Only theory sections become anchors. Cat A doesn't anchor
            # other Cat As — Cat A nests under its preceding Cat B.
            if _is_cat_b(s):
                y_top = 0.0
                if 1 <= ps <= pdf_total_pages:
                    try:
                        page = doc[ps - 1]
                    except Exception:
                        page = None
                    if page is not None:
                        if _page_has_extractable_text(page):
                            any_text = True
                        bbox = _find_heading_bbox_on_page(
                            page, s.get("title") or ""
                        )
                        if bbox is not None:
                            y_top = bbox[0]
                # Check if has Cat B descendants — if not it's a leaf-theory
                kids = s.get("subsections") or []
                has_theory_descendant = _has_theory_descendant(kids)
                out.append({
                    "section": s,
                    "id": s.get("id"),
                    "title": s.get("title"),
                    "depth": depth,
                    "page_start": ps,
                    "page_end": pe,
                    "y_top": y_top,
                    "is_leaf": not has_theory_descendant,
                })
            walk(s.get("subsections") or [], depth + 1)

    walk(sections, 0)
    return out, any_text


def _has_theory_descendant(sections: list) -> bool:
    for s in sections or []:
        if not isinstance(s, dict):
            continue
        if _is_cat_b(s):
            return True
        if _has_theory_descendant(s.get("subsections") or []):
            return True
    return False


def _find_section_by_id(sections: list, target_id: str) -> tuple[dict | None, list[dict] | None]:
    """Locate (section, siblings_list_containing_it). Returns (None, None) if not found."""
    if target_id is None:
        return (None, None)

    def walk(siblings):
        for s in siblings or []:
            if not isinstance(s, dict):
                continue
            if s.get("id") == target_id:
                return (s, siblings)
            kids = s.get("subsections") or []
            hit = walk(kids)
            if hit[0] is not None:
                return hit
        return (None, None)

    return walk(sections)


def _collect_cat_a_with_parent(
    sections: list,
) -> list[tuple[dict, dict | None, list]]:
    """Walk tree, yielding (cat_a_section, parent_section_or_None, siblings_list).

    parent is None for top-level Cat A. siblings is the list the Cat A
    currently lives in (so we can remove it).
    """
    out: list[tuple[dict, dict | None, list]] = []

    def walk(siblings: list, parent: dict | None):
        for s in siblings or []:
            if not isinstance(s, dict):
                continue
            if _is_cat_a(s):
                out.append((s, parent, siblings))
            # Always recurse — Cat A can contain Cat A children that
            # would also need positional checks; though rare.
            walk(s.get("subsections") or [], s)

    walk(sections, None)
    return out


def sanitize_cat_a_by_position(
    schema_dict: dict,
    pdf_bytes: bytes,
) -> tuple[dict, SanitizeReport]:
    """Positional Cat A reparenting using PDF heading bboxes.

    See module docstring for the algorithm.

    Falls back silently to a no-op if:
      * pymupdf cannot open pdf_bytes
      * the PDF has no extractable text on any sampled page (scanned)

    Idempotent.
    """
    new_schema = copy.deepcopy(schema_dict)
    report = SanitizeReport()

    try:
        import pymupdf as fitz
    except Exception:
        try:
            import fitz  # type: ignore
        except Exception:
            logger.warning("pymupdf not available — skipping positional Cat A pass")
            return new_schema, report

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        logger.warning("pymupdf could not open PDF for Cat A positional pass: %s", e)
        return new_schema, report

    try:
        pdf_total_pages = doc.page_count
        sections = new_schema.get("sections") or []

        # Step 1 — collect theory anchors with bboxes
        anchors, any_text = _collect_theory_anchors(
            sections, doc, pdf_total_pages=pdf_total_pages,
        )

        if not any_text:
            # Scanned PDF — positional pass is meaningless. Bail.
            report.positional_skipped_no_text = True
            logger.info(
                "Cat A positional pass: scanned PDF (no extractable text), "
                "skipping — sibling layer still applies"
            )
            return new_schema, report

        # Step 2 — for each Cat A, find its own heading bbox + correct parent
        cat_a_entries = _collect_cat_a_with_parent(sections)

        for cat_a, current_parent, current_siblings in cat_a_entries:
            ps = cat_a.get("page_start")
            if not isinstance(ps, int) or isinstance(ps, bool):
                continue
            if not (1 <= ps <= pdf_total_pages):
                continue

            # Find Cat A's own bbox on its claimed page
            try:
                page = doc[ps - 1]
            except Exception:
                continue
            bbox = _find_heading_bbox_on_page(page, cat_a.get("title") or "")
            # If we can't locate the Cat A's own heading bbox, we have no
            # reliable signal for same-page tie-breaks — skip rather than
            # guess. (Cross-page mismatches are still caught by the page-
            # range guard below since we don't need y for those.)
            if bbox is None:
                cat_a_y_top = None
            else:
                cat_a_y_top = bbox[0]

            # CONSERVATIVE GUARD: only reparent when EITHER
            #   (a) the current parent's page range does NOT contain the Cat A's
            #       page_start (cross-page mismatch — clear bug), OR
            #   (b) same-page tie-break: there exists ANOTHER theory section
            #       whose page_start == cat_a.page_start AND whose own heading
            #       bbox is BELOW the Cat A's bbox. (Then Cat A is above the
            #       next theory heading on its starting page → should attach
            #       to the previous theory section per §3.2.6.)
            # Otherwise leave the placement alone. This prevents the false
            # positive where a Cat A correctly nested under "1.3 Cartesian
            # Product" (p2-4) gets pulled into a deeper Cat-B sibling that
            # also happens to contain the page.
            current_parent_contains = False
            if current_parent is not None and _is_cat_b(current_parent):
                pp_ps = current_parent.get("page_start")
                pp_pe = current_parent.get("page_end")
                if (
                    isinstance(pp_ps, int) and not isinstance(pp_ps, bool)
                    and isinstance(pp_pe, int) and not isinstance(pp_pe, bool)
                    and pp_ps <= ps <= pp_pe
                ):
                    current_parent_contains = True

            # Detect same-page-above signal:
            #   (a) current_parent_contains AND current parent's own heading
            #       is printed BELOW Cat A on Cat A's starting page → wrong
            #       parent (Cat A is above the section heading).
            #   (b) cross-page mismatch (current parent's range doesn't
            #       contain Cat A's page) — always counts as a "needs to
            #       move" trigger, regardless of same-page geometry.
            # Case (b) becomes the cross-page reference fix from the
            # problem statement.
            same_page_above_signal = False
            cross_page_mismatch = not current_parent_contains
            if cat_a_y_top is not None and current_parent_contains:
                pp_ps = current_parent.get("page_start")
                current_parent_anchor = next(
                    (a for a in anchors if a["section"] is current_parent),
                    None,
                )
                if (
                    pp_ps == ps
                    and current_parent_anchor is not None
                    and current_parent_anchor["y_top"] > 0.0
                    and current_parent_anchor["y_top"] > cat_a_y_top
                ):
                    same_page_above_signal = True

            if current_parent_contains and not same_page_above_signal:
                continue

            # At this point we'll attempt to find a better parent.
            # Treat both same_page_above_signal and cross_page_mismatch
            # as "carry-over preferred" triggers — we want the immediately
            # preceding theory section.
            use_carry_over_branch = same_page_above_signal or cross_page_mismatch
            # If bbox not found AND it's not page-start tie territory,
            # we still try the positional logic with y_top=0.0 — that
            # forces "ABOVE any theory heading on this page" which
            # naturally maps Cat A back to the previous-page parent
            # carry-over, which is exactly the same-page tie behaviour.

            # Build candidate set. Two categories:
            #   (A) containment candidates: theory whose page range contains
            #       (ps, cat_a_y_top). Same-page constraint: anchor's y_top
            #       must be ≤ cat_a_y_top (heading came BEFORE the Cat A).
            #   (B) carry-over candidates: theory section whose page_end is
            #       directly before ps (page_end == ps - 1 OR page_end == ps
            #       with y_top ≤ cat_a_y_top but that's already in A). This
            #       handles the same-page-above case where Heading 1 ends on
            #       p2 and Cat A sits at top of p3 ABOVE the next heading
            #       which starts mid-page.
            # For the candidate scoring below, use 0.0 as a safe lower
            # bound when we couldn't locate the Cat A's bbox — at that
            # point we're only reachable via cross-page mismatch (the
            # current_parent_contains guard above rejects same-page
            # noise without a bbox).
            cat_a_y_for_rank = cat_a_y_top if cat_a_y_top is not None else 0.0

            containment: list[dict] = []
            for a in anchors:
                if a["section"] is cat_a:
                    continue
                a_ps = a["page_start"]
                a_pe = a["page_end"]
                # Same-page-as-Cat-A but heading below us → SKIP (we're above it)
                if a_ps == ps and a["y_top"] > cat_a_y_for_rank:
                    continue
                if a_ps <= ps <= a_pe:
                    containment.append(a)

            # Cross-page mismatch OR same-page-above → use carry-over branch.
            same_page_below_anchor = use_carry_over_branch

            best: dict | None = None
            if same_page_below_anchor:
                # Carry-over case: the parent must be a theory section that
                # ends ON or BEFORE ps and is the LAST one in document order
                # before the Cat A. Prefer page_end == ps-1 (clean carry),
                # then page_end == ps with y_top above Cat A.
                # PREFERENCE ORDER for the previous theory:
                #   (1) theory whose page_end == ps - 1 (clean carry across
                #       page boundary). Pick deepest among these.
                #   (2) theory whose range strictly contains ps (with same-
                #       page heading ABOVE Cat A). Pick deepest among these.
                # Crucially, (1) BEATS (2) — a section that ended on the
                # previous page is a better "carry over" parent than the
                # chapter wrapper that happens to span the whole book.
                clean_carry = [
                    a for a in anchors
                    if a["section"] is not cat_a
                    and a["page_end"] == ps - 1
                ]
                if clean_carry:
                    clean_carry.sort(key=lambda a: a["depth"], reverse=True)
                    best = clean_carry[0]
                else:
                    contain_carry = [
                        a for a in anchors
                        if a["section"] is not cat_a
                        and a["page_start"] <= ps <= a["page_end"]
                        and (a["page_start"] != ps or a["y_top"] <= cat_a_y_for_rank)
                        # Exclude the current (mis-)parent — we know it's
                        # the wrong one (same_page_above_signal fired).
                        and a["section"] is not current_parent
                    ]
                    if contain_carry:
                        contain_carry.sort(
                            key=lambda a: (a["depth"], a["page_end"]),
                            reverse=True,
                        )
                        best = contain_carry[0]

            if best is None and containment:
                # Standard containment path. Deepest leaf wins; on same-page
                # ties, the one whose y_top is closest below Cat A's y_top.
                def _rank(a: dict) -> tuple:
                    # Higher score wins.
                    same_page_score = (
                        a["y_top"] if a["page_start"] == ps else -1.0
                    )
                    return (a["depth"], 1 if a["is_leaf"] else 0, same_page_score)

                # NOTE: we keep depth-deepest preference here because by
                # this branch the current parent does NOT contain the page
                # (guarded above). In that case picking the deepest
                # specific theory whose range covers the page is the right
                # move; the false positive of "Cat A → Activity 1 (deeper
                # sibling at same page)" cannot occur here.

                containment.sort(key=_rank, reverse=True)
                best = containment[0]

            if best is None:
                continue

            # Determine the current effective parent id
            current_parent_id = (
                current_parent.get("id") if current_parent else None
            )
            target_parent_id = best["id"]

            if current_parent_id == target_parent_id:
                continue  # already correctly parented

            # Reparent: remove from current_siblings, append to target's subsections
            try:
                current_siblings.remove(cat_a)
            except ValueError:
                continue
            target_section = best["section"]
            target_section.setdefault("subsections", []).append(cat_a)
            report.positional_reparented += 1
            report.moves.append({
                "cat_a_id": cat_a.get("id"),
                "cat_a_title": cat_a.get("title"),
                "cat_a_page": ps,
                "cat_a_y_top": cat_a_y_top,
                "from_parent_id": current_parent_id or "ROOT",
                "from_parent_title": current_parent.get("title") if current_parent else None,
                "moved_under_id": target_parent_id,
                "moved_under_title": best["title"],
                "moved_under_pages": [best["page_start"], best["page_end"]],
                "layer": "positional",
            })

        new_schema["sections"] = sections
        return new_schema, report
    finally:
        try:
            doc.close()
        except Exception:
            pass


def sanitize_cat_a_nesting(
    schema_dict: dict,
    pdf_bytes: bytes | None = None,
) -> tuple[dict, SanitizeReport]:
    """Deterministic post-pass that fixes Cat A nesting.

    Two layers:
      1. Sibling layer (always): moves Cat A under preceding Cat B sibling.
      2. Positional layer (if pdf_bytes): uses PDF bboxes to verify Cat A's
         parent matches the theory section that physically contains its
         printed heading.

    Returns (new_schema_dict, report). Input is NOT mutated.
    Idempotent — second run produces no changes.
    """
    new_schema = copy.deepcopy(schema_dict)
    report = SanitizeReport()
    # Layer 1: sibling.
    root = {"id": "ROOT", "subsections": new_schema.get("sections") or []}
    _sanitize_children(root, report)
    new_schema["sections"] = root["subsections"]

    # Layer 2: positional (PDF-driven).
    if pdf_bytes:
        new_schema, pos_report = sanitize_cat_a_by_position(new_schema, pdf_bytes)
        # Merge positional results into the same report object.
        report.positional_reparented = pos_report.positional_reparented
        report.positional_skipped_no_text = pos_report.positional_skipped_no_text
        report.moves.extend(pos_report.moves)

    return new_schema, report


__all__ = [
    "sanitize_cat_a_nesting",
    "sanitize_cat_a_by_position",
    "SanitizeReport",
]
