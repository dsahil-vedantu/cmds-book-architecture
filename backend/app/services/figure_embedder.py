"""Deterministic post-extraction figure-to-content embedder.

Runs CPU-only (no Gemini, no AI). Takes the figures the extractor
already pulled out of the PDF, matches each to its placeholder
position inside the theory body's blocks (or the question's
raw_text), and writes the placement metadata into
``figure_references``.

Phase 1 of the image-integration plan. Section A of the build.

Why this exists
---------------
The theory extractor produces theory blocks like
   {"t": "fig", "c": "Figure 4.7"}              (anchor mention)
   {"t": "p",   "c": "see Fig. 4.7 for the chart"}
The figure extractor produces ``figures`` rows with
   normalized_label = "4.7", figure_number = "Figure 4.7",
   image_bytes / regen_image_bytes, etc.
Without an explicit join, the frontend doesn't know which image to
drop into which slot. This embedder writes that join into
``figure_references``:
  - ``placement_kind``         "inline" | "appended" | "needs_review"
  - ``placement_block_idx``    for theory: the section.blocks index
                                 the figure should be rendered AFTER
                                 (or AT if the matched block is the
                                 fig placeholder itself)
  - ``placement_char_offset``  for question: the byte offset into
                                 question.raw_text where the inline
                                 figure marker is rendered

Matching is fuzzy but deterministic. Same input always produces the
same output.

The embedder NEVER writes to sections.blocks or questions.raw_text.
Theory and question extractors stay the source of truth for their
own data.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.figure import Figure
from app.models.figure_reference import FigureReference
from app.models.question import Question
from app.models.section import Section

logger = logging.getLogger(__name__)


# Label normalization — turn any of "Figure 4.7", "Fig. 4.7", "fig 4.7",
# "FIGURE 4.7", "[Figure 4.7]", "(Fig 4.7)" into the canonical "4.7" for
# comparison. Mirrors the regex the linker uses for similar work.
_LABEL_PREFIX_RE = re.compile(
    r"\b(figures?|figs?\.?|fig\s*no\.?|table)\s*",
    re.IGNORECASE,
)


def _normalize_label(text: str | None) -> str:
    """Extract a figure label's NUMBER component.

    Examples:
      "Figure 9.1"                  → "9.1"
      "Figure 9.1 Discharge tube"   → "9.1"   (caption ignored — the
                                                index must key by the
                                                number alone so figure
                                                entities with label
                                                "Figure 9.1" line up
                                                with fig blocks that
                                                include the caption)
      "Fig. 4.7a"                   → "4.7a"
      "(Figure 9.10)"               → "9.10"
      "Table 9.1"                   → "9.1"
      ""                            → ""
    """
    if not text:
        return ""
    # Strip "Figure"/"Fig."/"Table" prefix first, then look for the
    # number — accepts "9", "9.1", "9.1.2", optionally followed by a
    # single trailing letter (e.g. "4.7a").
    s = _LABEL_PREFIX_RE.sub("", str(text).strip())
    m = re.search(r"(\d+(?:\.\d+)*[a-z]?)", s, re.IGNORECASE)
    return m.group(1).lower() if m else ""


def _build_label_pattern(label: str) -> re.Pattern[str] | None:
    """Compile a regex that finds "<prefix> {label}" or just "{label}" in text.

    Uses word boundaries to avoid matching "4.7" inside "14.75". The label
    itself is escaped so "4.10" is matched literally (not as regex).
    """
    if not label:
        return None
    esc = re.escape(label)
    # OCR-tolerant matcher. Accepts (case-insensitive):
    #   "Figure 4.7", "Figures 4.7", "Fig 4.7", "Fig. 4.7", "Fig.4.7",
    #   "Fig_4.7", "Fig:4.7", "Figure-4.7", "Figure4.7" (no separator),
    #   "figure no. 4.7", "(Fig 4.7)", "[Figure 4.7]".
    # Separator class: whitespace / dot / underscore / colon / dash (zero+).
    pat = (
        # Tolerant of: parens / brackets around the number, underscore /
        # colon / dash separators, optional "no." between the keyword and
        # the number. Examples that match:
        #   "Figure 9.2", "Fig 9.2", "Fig. 9.2", "Fig.9.2",
        #   "Fig_9.2", "Fig:9.2", "Figure-9.2", "Figures 9.2",
        #   "figure no. 9.2", "(Fig 9.2)", "[Figure 9.2]",
        #   "Fig. (9.2)", "Fig.(9.2)"  ← the parenthesised forms
        r"(?:\bfigures?\s*(?:no\.?)?|\bfigs?\.?)[\s._:\-]*[(\[]?\s*"
        + esc
        + r"\s*[)\]]?\b"
    )
    try:
        return re.compile(pat, re.IGNORECASE)
    except re.error as e:
        logger.warning("regex compile failed for label %r: %s", label, e)
        return None


def _select_variant(fig: Figure) -> str:
    """Decide which image variant to surface: regen (if approved) else original."""
    if fig.regen_image_bytes and fig.approved_at is not None:
        return "regen"
    return "original"


def _block_text_pool(b: Any) -> str:
    """Flatten EVERY searchable text field of a theory block into one
    concatenated string for anchor / label matching.

    Covers all block types from the theory extractor schema:

      body / heading / key_point / list_item        → c
      equation (single)                              → c
      equation (multi-line)                          → eqs[]
      definition                                     → term + c
      figure (placeholder in theory body)            → label + c (caption)
      table                                          → caption + headers[]
                                                       + rows[][]
      example_ref / exercise_ref / question_ref      → label + number
      generic legacy / future blocks                 → content + ref + items[]

    Without this pool, the embedder's anchor-match only saw block.c —
    so anchors referring to table cells, equation arrays, ref numbers,
    or definition terms silently fell through to page_fallback.
    """
    if not isinstance(b, dict):
        return ""
    parts: list[str] = []
    # Definition blocks: the printed (and figure-anchor) form is
    # "term: content" — e.g. "Line: A line is a set of infinite points…".
    # Emit that term-first reconstruction FIRST so a figure anchor of the
    # same shape matches as a contiguous substring. The generic field loop
    # below appends `term` AFTER `c`, which yields "A line is… Line"
    # (term last) and breaks the substring match — this is the regression
    # that dropped definition figures to page_fallback. Restores the prior
    # embedder behavior (f"{term}: {c}").
    if b.get("t") == "def":
        _term = b.get("term")
        _dc = b.get("c") or b.get("content")
        if isinstance(_term, str) and _term and isinstance(_dc, str) and _dc:
            parts.append(f"{_term}: {_dc}")
    # Scalar string fields (every block type's primary text)
    for k in ("c", "content", "label", "term", "caption", "number", "ref"):
        v = b.get(k)
        if isinstance(v, str) and v:
            parts.append(v)
        elif isinstance(v, (int, float)):
            parts.append(str(v))
    # Multi-line equation array
    eqs = b.get("eqs")
    if isinstance(eqs, list):
        for x in eqs:
            if isinstance(x, str) and x:
                parts.append(x)
    # List items (when stored as array instead of individual list_item blocks)
    items = b.get("items")
    if isinstance(items, list):
        for x in items:
            if isinstance(x, str) and x:
                parts.append(x)
    # Table headers (array of column names)
    headers = b.get("headers")
    if isinstance(headers, list):
        for x in headers:
            if isinstance(x, str) and x:
                parts.append(x)
            elif isinstance(x, (int, float)):
                parts.append(str(x))
    # Table rows (2D array of cell values)
    rows = b.get("rows")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, list):
                for x in row:
                    if isinstance(x, str) and x:
                        parts.append(x)
                    elif isinstance(x, (int, float)):
                        parts.append(str(x))
    return " ".join(parts)


def _question_body_target(fig: Figure, q: Any) -> tuple[str, int]:
    """F1/F2 — route a question figure into raw_text vs solution_text.

    Returns (context, char_offset) for building a FigureReference:
      body_type == "solution" → ("solution", len(q.solution_text))
                                 (figure sits inside the worked solution)
      else (body_type == "question" or None / legacy)
                              → ("question", len(q.raw_text))
                                 (figure sits inside the question stem)

    The position is the END of the body — the renderer / composer treats
    figure_references as appended placeholders at that offset.
    """
    body_type = getattr(fig, "body_type", None)
    if body_type == "solution":
        return ("solution", len(q.solution_text or ""))
    return ("question", len(q.raw_text or ""))


def _find_inline_block_index(
    blocks: list[Any],
    normalized_label: str,
    label_pattern: re.Pattern[str] | None,
) -> int | None:
    """Scan section.blocks for the best inline anchor for this figure.

    Priority:
      1. A ``fig`` block whose ``c`` text matches the label
         (e.g. ``{"t": "fig", "c": "Figure 4.7"}``)
      2. A paragraph / equation / list block that mentions the label
         in its ``c`` text (e.g. "see Fig. 4.7 for...")

    Returns the index of the matched block, or None if no match.
    """
    if not isinstance(blocks, list) or not blocks:
        return None

    # Priority 1: fig blocks — match against BOTH `label` and `c`
    # because the OCR's fig blocks store the figure number in `label`
    # (e.g. {"t":"fig","label":"Figure 8.5","c":"Pith ball electroscope"}).
    for i, b in enumerate(blocks):
        if not isinstance(b, dict):
            continue
        if b.get("t") != "fig":
            continue
        for field in ("label", "c"):
            v = b.get(field) or ""
            if not isinstance(v, str) or not v:
                continue
            if _normalize_label(v) == normalized_label:
                return i
            if label_pattern and label_pattern.search(v):
                return i

    # Priority 2: any text block that mentions the label
    if label_pattern is None:
        return None
    for i, b in enumerate(blocks):
        if not isinstance(b, dict):
            continue
        c = b.get("c") or b.get("content") or ""
        if not isinstance(c, str):
            continue
        if label_pattern.search(c):
            return i

    return None


def _find_inline_char_offset(
    raw_text: str | None,
    label_pattern: re.Pattern[str] | None,
) -> int | None:
    """Find the character offset of the label mention in question raw_text."""
    if not raw_text or not label_pattern:
        return None
    m = label_pattern.search(raw_text)
    if m:
        return m.start()
    return None


def _build_global_label_index(
    sections: list[Any],
) -> dict[str, list[tuple[str, int, str]]]:
    """Scan EVERY section's blocks once and build a global index from
    normalized_label -> [(section_id, block_idx, source), ...].

    `source` is "fig" when the match came from a ``{"t": "fig"}`` block
    (high-confidence anchor — Gemini's theory extractor saw a labelled
    placeholder there) or "text" when the match came from a paragraph /
    equation / list mention (medium-confidence — could be a "see Fig. 4.7"
    reference rather than the figure's actual location).

    This is the foundation for Pass 1 label-first matching: when a figure
    has normalized_label "4.1", the embedder consults this index to find
    where in the book "Figure 4.1" is actually mentioned in the theory
    body — independent of the figure's section_id (which the figure
    linker assigned by PAGE NUMBER and can be wrong when sections share
    pages).
    """
    out: dict[str, list[tuple[str, int, str]]] = {}
    for sec in sections:
        section_id = sec.section_id
        blocks = sec.blocks or []
        if not isinstance(blocks, list):
            continue
        # Pass A: fig blocks (highest confidence). Check BOTH `label` AND
        # `c` because the OCR sometimes puts "Figure 8.5" in `label` and
        # the caption in `c` — we must index by EITHER to find the figure.
        for i, b in enumerate(blocks):
            if not isinstance(b, dict):
                continue
            if b.get("t") != "fig":
                continue
            seen_for_block: set[str] = set()
            for field in ("label", "c"):
                v = b.get(field) or ""
                if not isinstance(v, str) or not v:
                    continue
                norm = _normalize_label(v)
                if norm and norm not in seen_for_block:
                    seen_for_block.add(norm)
                    out.setdefault(norm, []).append((section_id, i, "fig"))
        # Pass B: text mentions (medium confidence) — captured as
        # "<context>label" e.g. "Fig 4.7" inside a paragraph
        for i, b in enumerate(blocks):
            if not isinstance(b, dict):
                continue
            if b.get("t") == "fig":
                continue  # already handled in Pass A
            c = b.get("c") or b.get("content") or ""
            if not isinstance(c, str) or not c:
                continue
            # find all label-like substrings via a generic pattern.
            # Tolerant: allows optional parens / brackets around the number
            # (e.g. "Fig. (9.2)", "[Figure 9.2]") AND underscore/colon
            # separators (e.g. "Fig_9.2", "Fig:9.2") — same separator set
            # the frontend uses.
            for m in re.finditer(
                r"\bfigs?(?:ure)?\.?[\s._:\-]*[(\[]?\s*(\d+(?:\.\d+)?[a-z]?)\s*[)\]]?\b",
                c,
                flags=re.IGNORECASE,
            ):
                norm = _normalize_label(m.group(0))
                if norm:
                    out.setdefault(norm, []).append((section_id, i, "text"))
    return out


def _pick_label_match(
    candidates: list[tuple[str, int, str]],
    figure_page: int | None,
    sections_by_id: dict[str, Any],
) -> tuple[str, int] | None:
    """Pick the best (section_id, block_idx) match from a list of
    label-index candidates.

    Tie-breaking:
      1. Prefer "fig" source over "text" source (an actual placeholder
         block beats a paragraph reference).
      2. If figure_page is known, prefer the candidate whose section's
         page range contains the figure's page.
      3. Otherwise first match wins (stable).
    """
    if not candidates:
        return None
    # Bucket by source priority
    fig_candidates = [c for c in candidates if c[2] == "fig"]
    text_candidates = [c for c in candidates if c[2] != "fig"]
    pool = fig_candidates or text_candidates
    if not pool:
        return None
    # Page-based tie-break inside the priority bucket
    if figure_page is not None:
        page_matches = []
        for section_id, block_idx, _ in pool:
            sec = sections_by_id.get(section_id)
            ps = getattr(sec, "page_start", None) if sec else None
            pe = getattr(sec, "page_end", None) if sec else None
            if ps is not None and pe is not None and ps <= figure_page <= pe:
                page_matches.append((section_id, block_idx))
        if page_matches:
            return page_matches[0]
    section_id, block_idx, _ = pool[0]
    return section_id, block_idx


# ─── DIFF / UPSERT (debt #44) ─────────────────────────────────────
# Replaces the legacy wipe-and-rebuild pattern in both wrappers.
# Compares computed target placements against existing DB rows and
# emits only the deltas (insert/update/delete). Benefits:
#   1. Eliminates the 600ms wipe window where API reads return zero
#      figures (the "figures flicker" symptom on uploads with 6 tail
#      embedder calls).
#   2. Preserves user manual placements (link_method='manual') so QA
#      work survives subsequent embedder runs — debt #55.
#   3. Preserves is_hidden flag (user-clicked-X stays hidden through
#      re-embed cycles).
#   4. Idempotent — re-run with no changes = zero DB writes.
#
# Identity key per ref: (figure_id, context, question_id)
#   figure_id    : the underlying Figure row
#   context      : 'theory' vs 'question' — dual-context figures have
#                  TWO refs (one in theory body, one beside its question)
#   question_id  : distinguishes per-question refs (None for theory)

def _apply_placement_diff(
    existing_refs: list[FigureReference],
    target_refs: list[FigureReference],
) -> tuple[
    list[FigureReference],
    list[tuple[FigureReference, FigureReference]],
    list[FigureReference],
]:
    """Diff target placements against existing refs. Returns
    (to_insert, to_update_pairs, to_delete). Caller persists.

    Preservation rules:
      - link_method='manual'  → never touched (insert/update/delete)
      - is_hidden carries across updates
    """
    def _key(r):
        return (r.figure_id, r.context, r.question_id)

    existing_by_key = {_key(r): r for r in existing_refs}
    target_by_key = {_key(r): r for r in target_refs}

    to_insert: list[FigureReference] = []
    to_update: list[tuple[FigureReference, FigureReference]] = []
    to_delete: list[FigureReference] = []

    # New + updated
    for k, target in target_by_key.items():
        existing = existing_by_key.get(k)
        if existing is None:
            to_insert.append(target)
            continue
        if existing.link_method == "manual":
            # User-placed — never overwrite their decision
            continue
        # Compare placement fields
        if (
            existing.section_ref != target.section_ref
            or existing.section_uuid != target.section_uuid
            or existing.placement_kind != target.placement_kind
            or existing.placement_block_idx != target.placement_block_idx
            or existing.placement_char_offset != target.placement_char_offset
            or existing.placeholder_text != target.placeholder_text
            or existing.body_target != target.body_target
        ):
            to_update.append((existing, target))

    # Removed (existing refs not in target)
    for k, existing in existing_by_key.items():
        if k in target_by_key:
            continue
        if existing.link_method == "manual":
            # Computed target dropped it BUT user placed it manually —
            # preserve. They get a "stale manual placement" badge in
            # the UI if/when we ship that, but data stays.
            continue
        to_delete.append(existing)

    return to_insert, to_update, to_delete


def _apply_update(existing: FigureReference, target: FigureReference) -> None:
    """Copy mutable placement fields from target onto existing in place.
    Preserves identity (id), is_hidden, created_at. Caller commits."""
    existing.section_ref = target.section_ref
    existing.section_uuid = target.section_uuid
    existing.placement_kind = target.placement_kind
    existing.placement_block_idx = target.placement_block_idx
    existing.placement_char_offset = target.placement_char_offset
    existing.placeholder_text = target.placeholder_text
    existing.body_target = target.body_target
    existing.link_method = "auto"  # explicit — this run is from the embedder


# ─── DETERMINISTIC TOP-DOWN PLACEMENT (v2) ────────────────────────
# Replaces the legacy multi-pass _compute_figure_placements_legacy
# (kept below, deprecated). Same input/output shape so call sites
# are unchanged. Logic uses the user's stated mental model:
#
# For ctx=question figures (labelled OR unlabelled):
#   Step 0  Identify section (Cat A)
#   Step 1  Identify question_id  (via question_ref or question_no
#                                  scoped to section's questions)
#   Step 2  Identify target text  (body_type "solution" → solution_text,
#                                  else raw_text)
#   Step 3  Identify position     (normalized_label match OR anchor_text
#                                  substring OR append)
#
# For ctx=theory figures (labelled OR unlabelled):
#   Step 0  Identify section (Cat B)
#   Step 1  Identify block        (label match → cross-section index →
#                                  anchor_text substring)
#   Step 2  Position relative to block (above / below per anchor_position)
#   Step 3  Fallback to page_fallback (section end) if nothing matched
#
# Key invariants this enforces:
#   - EVERY figure produces a FigureReference row (no silent drops).
#     Unresolvable cases get placement_kind="unattached" with a logged
#     reason — debt #43 satisfied.
#   - Per-figure try/except — one bad figure can't crash the whole run.
#   - Question resolution scoped to the section first (section_uuid →
#     questions); falls back to bank-wide search only if scoped fails.
#   - Stem-vs-solution routing actually uses body_type (now populated
#     after the raw_context fix in figures_tasks.py:_derive_body_type).

def _anchor_match_needles(norm_anchor: str) -> list[str]:
    """Exact-substring needles for matching a figure anchor against a block.

    GENERAL fix for the whole class of "the anchor text is right there but it
    didn't match" failures. An anchor often starts with math/symbols
    ("tan⁻¹(b/a)") that canonicalize DIFFERENTLY between the anchor (Unicode)
    and the block (LaTeX source). Matching only the anchor's PREFIX then fails
    even though the prose later in the anchor is a verbatim substring of the
    block.

    So we return the prefix windows AND exact sliding windows across the WHOLE
    anchor. The caller takes the first block containing ANY of these runs.
    Every needle is an exact substring of the normalized anchor (no fuzzy, no
    token-overlap) → it cannot false-positive onto an unrelated block; it just
    stops betting everything on the first 60 characters. General for any
    anchor whose distinctive text is not at the very start.
    """
    n = len(norm_anchor)
    if not n:
        return []
    if n <= 40:
        return [norm_anchor] if n <= 30 else [norm_anchor, norm_anchor[:30]]
    out = [norm_anchor[:60], norm_anchor[:30]]
    win, step = 40, 20
    for start in range(0, n - win + 1, step):
        out.append(norm_anchor[start:start + win])
    out.append(norm_anchor[-win:])  # always cover the tail
    seen: set[str] = set()
    uniq: list[str] = []
    for x in out:
        if x and x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def _compute_figure_placements(
    figures,
    sections_by_id,
    questions,
    questions_by_section,
    label_index,
    book_id: UUID,
):
    """TOP-DOWN deterministic placement — see module-level comment above."""
    counters: dict[str, int] = {
        "figures_seen": 0,
        "theory_inline": 0,
        "theory_appended": 0,
        "question_inline": 0,
        "question_appended": 0,
        "unattached": 0,
        "skipped_no_section": 0,
        "theory_relinked_by_label": 0,
        # Per-reason failure tallies — debt #43 observability foundation.
        "failed_section_not_found": 0,
        "failed_question_not_found": 0,
        "failed_exception": 0,
    }
    new_refs: list[FigureReference] = []
    failure_log: list[dict] = []

    # ── helpers ────────────────────────────────────────────────────
    def _norm_qno(s) -> str:
        if not s:
            return ""
        t = str(s).strip().lower()
        t = re.sub(r"^q\.?\s*", "", t)   # drop leading "Q" / "q."
        t = t.strip("().[]{} \t.")
        return t

    def _resolve_section(fig):
        """Step 0 — identify the section (slug + Section row).

        Resolution priority (E1 — orphan recovery):
          1. fig.section_id is set + matches a known section → use it.
          2. Page-based EXACT match: find section whose [page_start,
             page_end] contains fig.page_number.
          3. Page-based NEAREST match: pick section with smallest
             distance from fig.page_number to its range. Closes the
             "_orphan" gap when figure_section_resolver couldn't pick
             the right section but the figure clearly belongs near a
             specific page (observed: Quadratic Equations had section_id
             "_orphan" on ~22% of figs; the page_number was always set,
             schema's section page ranges didn't cleanly cover the page
             due to extraction quirks).
          4. Returns (None, None) only when neither slug nor page is
             available — true orphan, can't be recovered.
        """
        sid = (fig.section_id or "").strip()
        if sid and sid != "_orphan" and sid in sections_by_id:
            return sid, sections_by_id[sid]
        page = fig.page_number
        if page is None:
            return None, None
        # Exact page-range match first
        best_exact: tuple[str, Any] | None = None
        # Track nearest-by-distance for fallback
        nearest: tuple[int, str, Any] | None = None
        for sid_iter, sec_iter in sections_by_id.items():
            ps = getattr(sec_iter, "page_start", None)
            pe = getattr(sec_iter, "page_end", None)
            if not (isinstance(ps, int) and isinstance(pe, int)):
                continue
            if ps <= page <= pe:
                # Exact hit — prefer the section whose range is TIGHTEST
                # (smallest span). Avoids picking the chapter wrapper
                # when a tighter subsection also contains the page.
                span = pe - ps
                if best_exact is None or span < (
                    getattr(best_exact[1], "page_end", 0)
                    - getattr(best_exact[1], "page_start", 0)
                ):
                    best_exact = (sid_iter, sec_iter)
                continue
            # Compute distance from page to this section's range
            if page < ps:
                dist = ps - page
            else:
                dist = page - pe
            if nearest is None or dist < nearest[0]:
                nearest = (dist, sid_iter, sec_iter)
        if best_exact is not None:
            return best_exact
        if nearest is not None:
            # Nearest-section recovery — guards against schema page
            # range gaps. Distance >5 is suspiciously far; still return
            # but caller can choose to mark for review.
            return nearest[1], nearest[2]
        return None, None

    def _find_question(sec_slug: str, identifier: str):
        """Step 1 for ctx=question — find the question by identifier
        (question_ref e.g. "Q3" / "Example 5" OR question_no e.g. "3")
        scoped to the section's questions FIRST, then bank-wide, then
        section_ref slug suffix as the rescue path.

        Section-scoped lookup avoids false positives when chapters share
        question numbers (Ch5 Q3 vs Ch6 Q3). Bank-wide is the legacy path
        kept for cross-section references. Slug-suffix rescue handles the
        observed prod failure where question_number was empty but
        section_ref encoded the number ("...-example-9.11" → Q9.11).
        """
        want = _norm_qno(identifier)
        if not want:
            return None
        # Section-scoped (correct case)
        for q in questions_by_section.get(sec_slug, []):
            if _norm_qno(q.question_number) == want:
                return q
        # Bank-wide (legacy / cross-section refs)
        for q in questions:
            if _norm_qno(q.question_number) == want:
                return q
        # Section_ref slug suffix rescue
        for q in questions:
            sref = (q.section_ref or "").lower()
            if not sref:
                continue
            if sref.endswith(f"-example-{want}") or sref.endswith(f"-{want}") or sref.endswith(f"::{want}"):
                return q
        return None

    def _emit_unattached(fig, sec_slug, reason: str, ctx: str = "theory"):
        """Always-emit an unattached ref so no figure goes ref-less. The
        reason is logged for the per-figure failure log (debt #43)."""
        counters["unattached"] += 1
        counters[f"failed_{reason}"] = counters.get(f"failed_{reason}", 0) + 1
        failure_log.append({
            "figure_id": str(fig.id),
            "context_hint": getattr(fig, "context_hint", None),
            "page": getattr(fig, "page_number", None),
            "section_id": getattr(fig, "section_id", None),
            "reason": reason,
        })
        return FigureReference(
            figure_id=fig.id, book_id=book_id,
            section_ref=sec_slug or fig.section_id or "",
            context=ctx, question_id=None,
            placeholder_text=None, link_method="auto",
            placement_kind="unattached",
            placement_block_idx=None,
            placement_char_offset=None,
        )

    # ── per-figure loop ────────────────────────────────────────────
    for fig in figures:
        counters["figures_seen"] += 1
        try:
            ctx = (fig.context_hint or "theory").lower()
            sec_slug, sec_row = _resolve_section(fig)

            normalized_label = (
                fig.normalized_label
                or _normalize_label(fig.figure_number)
                or ""
            ).strip()
            label_pattern = (
                _build_label_pattern(normalized_label)
                if normalized_label else None
            )
            pos_meta = fig.regen_meta if isinstance(fig.regen_meta, dict) else {}

            # ═══════════════════════════════════════════════════════
            # ctx = question  →  find question, route by body_type
            # ═══════════════════════════════════════════════════════
            if ctx == "question":
                # Step 1: identify question — TOP-DOWN with FOUR paths:
                #
                # PATH 0 — PLACEHOLDER MATCH (highest confidence).
                #          The question worker captures the figure's
                #          inline position as "{{fig: Figure X.Y}}" or
                #          "{{fig: (unlabelled diagram) — <desc>}}" in
                #          question.raw_text or question.solution_text.
                #          These placeholders are GROUND TRUTH from the
                #          PDF — they say "Figure 6.15 belongs here in
                #          this exact question's body or solution."
                #          We scan all questions for the placeholder
                #          BEFORE falling back to section_uuid / id paths.
                #          This corrects for figure_section_resolver
                #          misassignments where a page hosts multiple
                #          example sections (observed on Geometry 5 Pgs:
                #          Page 4 has both Ex 6.1 + Ex 6.2; resolver put
                #          all page-4 figs on Ex 6.1).
                #
                #
                # PATH A — UUID-direct (catches LABELLED figs without
                #          question_ref captured). When the figure sits
                #          in a single-question section (e.g.
                #          "...-example-6.1") its section_uuid uniquely
                #          maps to that ONE question. No identifier
                #          needed. This is the case the labelled-figure
                #          embedder failed before (Figures 6.15-6.18
                #          all section_id="...-example-X" but never
                #          attached because question_ref was missing).
                #
                # PATH B — identifier match (current behavior). Gemini
                #          sets question_ref ("Q3", "Example 5") for
                #          LABELLED figs and question_no ("3") for
                #          UNLABELLED. _find_question normalises both.
                #
                # PATH C — single-question section by slug ending
                #          (rescue for legacy data where section_uuid
                #          might be missing on the figure).
                q = None
                # Pre-computed placement from PATH 0 (placeholder match)
                # — when this fires, body_type is overridden by where the
                # placeholder was actually found (raw_text vs solution_text)
                # AND placement_char_offset is pinned to the placeholder's
                # exact char position (no need to re-scan in Step 3).
                forced_target = None  # "question" | "solution" | None
                forced_offset: int | None = None

                # PATH 0: LABELLED QUESTION — find the figure's label
                # anywhere in any question's raw_text or solution_text
                # using the SAME canonical matcher the theory PATH uses
                # (`_build_label_pattern(normalized_label)`). This is the
                # simple consistent rule: label + content_type → place
                # wherever the label appears in the appropriate text pool.
                #
                # The canonical matcher tolerates:
                #   - Wrapped placeholder:   `{{fig: Figure 6.16}}`
                #   - With description:      `{{fig: Figure 6.16 — desc}}`
                #     (the bug that mis-routed Figure 6.16 → Q 6.1 instead
                #     of Q 6.2 — exact-needle `{{fig: Figure 6.16}}` missed
                #     the description suffix)
                #   - Plain mention:         "see Figure 6.16"
                #   - Variant prefixes:      "Fig 6.16", "Fig. 6.16",
                #                            "(Figure 6.16)", "FIGURE 6.16"
                #   - Number-only key:       matcher is built from
                #                            `_normalize_label(fig.figure_number)`
                #                            so format inconsistency between
                #                            workers ("Figure 6.16" vs
                #                            "Fig. 6.16") never breaks the
                #                            match.
                # Word-boundary safe: pattern for "6.1" will NOT match
                # inside "6.16" and vice versa.
                #
                # Routing semantics preserved:
                #   raw_text match     → forced_target = "question"
                #   solution_text match → forced_target = "solution"
                # raw_text checked first so a label appearing in BOTH
                # routes to the question stem (the more visible location).
                if fig.figure_number and normalized_label and label_pattern is not None:
                    for qq in questions:
                        if qq.raw_text:
                            m = label_pattern.search(qq.raw_text)
                            if m:
                                q = qq
                                forced_target = "question"
                                forced_offset = m.start()
                                break
                        if qq.solution_text:
                            m = label_pattern.search(qq.solution_text)
                            if m:
                                q = qq
                                forced_target = "solution"
                                forced_offset = m.start()
                                break
                else:
                    # UNLABELLED: structured top-down — no anchor-text
                    # searching. The figure already carries:
                    #   context_hint = "question"           (it IS a Q fig)
                    #   section_uuid                        (which Q section)
                    #   regen_meta.question_no              (which Q number)
                    #   body_type = "question" | "solution" (stem vs sol)
                    # These fields are deterministic. Just look up the Q
                    # via section_uuid → question_no, then append to the
                    # right body based on body_type. No string matching.
                    question_no = (pos_meta.get("question_no") or "").strip()
                    fig_sec_uuid = getattr(fig, "section_uuid", None)

                    # PRIORITY 1: question_no is the user-facing ground
                    # truth from Gemini ("this fig belongs to Q 6.4").
                    # Use it FIRST, even if section_uuid points elsewhere
                    # — section_uuid can be wrong when figure_section_
                    # resolver mistakenly grouped figures by page (e.g.
                    # page 5 has both Ex 6.3 and Ex 6.4; resolver puts
                    # all p5 figs on Ex 6.3 even though Gemini said 6.4).
                    if question_no:
                        want = _norm_qno(question_no)
                        matches = [
                            qq for qq in questions
                            if _norm_qno(qq.question_number) == want
                        ]
                        if matches:
                            q = matches[0]

                    # PRIORITY 2: fallback to section_uuid → single Q.
                    # Only when no question_no was available.
                    if q is None and fig_sec_uuid is not None:
                        matches = [
                            qq for qq in questions
                            if qq.section_uuid is not None
                            and str(qq.section_uuid) == str(fig_sec_uuid)
                        ]
                        if len(matches) == 1:
                            q = matches[0]

                    if q is not None:
                        # body_type carries which body to append to.
                        # Default to "question" when missing.
                        forced_target = (
                            "solution"
                            if getattr(fig, "body_type", None) == "solution"
                            else "question"
                        )
                        # forced_offset stays None → Step 3 appends at end
                        # of the target text.

                # PATH A: section_uuid direct match — single-question section
                if q is None:
                    fig_sec_uuid = getattr(fig, "section_uuid", None)
                    if fig_sec_uuid is not None:
                        matching_qs = [
                            qq for qq in questions
                            if qq.section_uuid is not None
                            and str(qq.section_uuid) == str(fig_sec_uuid)
                        ]
                        if len(matching_qs) == 1:
                            q = matching_qs[0]

                # PATH B: identifier match
                if q is None:
                    identifier = (
                        pos_meta.get("question_ref")
                        or pos_meta.get("question_no")
                        or ""
                    ).strip()
                    if identifier:
                        q = _find_question(sec_slug or "", identifier)

                # PATH C: single-question by section_ref slug
                if q is None and sec_slug:
                    same_section_qs = [
                        qq for qq in questions
                        if qq.section_ref == sec_slug
                    ]
                    if len(same_section_qs) == 1:
                        q = same_section_qs[0]

                if q is None:
                    # No question matched after PATH 0 / A / B / C.
                    # SECTION-END FALLBACK (per "fig should never be lost"
                    # rule): if we DO know the section the figure belongs
                    # to, dump it at section end as a theory-context
                    # page_fallback. The figure stays visible in the UI
                    # (rendered at the end of its Cat A section, marked
                    # for human review). Without this fallback the
                    # figure would land in placement_kind="unattached"
                    # which the readers FILTER OUT → visible figure loss
                    # in production (observed on Quadratic Equations:
                    # 8 question figs invisible because q_no didn't
                    # resolve).
                    #
                    # If section also can't be resolved → true orphan,
                    # emit unattached (figure has no home at all).
                    if sec_row is not None:
                        new_refs.append(FigureReference(
                            figure_id=fig.id, book_id=book_id,
                            section_ref=sec_slug or fig.section_id or "",
                            # context="theory" so the theory-tab reader
                            # surfaces it; the Question tab reader filters
                            # by question_id which is None here.
                            context="theory", question_id=None,
                            placeholder_text=None, link_method="auto",
                            placement_kind="page_fallback",
                            placement_block_idx=None,
                            placement_char_offset=None,
                            body_target=None,
                        ))
                        counters["theory_appended"] += 1
                        failure_log.append({
                            "figure_id": str(fig.id),
                            "context_hint": "question",
                            "reason": "question_not_found_section_end_fallback",
                            "section": sec_slug or fig.section_id,
                            "page": fig.page_number,
                        })
                        continue
                    # No section either — true orphan
                    new_refs.append(_emit_unattached(
                        fig, sec_slug, "question_not_found", ctx="question",
                    ))
                    continue

                # Step 2: pick target text. PATH 0 placeholder match wins
                # (forced_target was set by where the placeholder lived).
                # Otherwise fall back to body_type from Gemini extraction
                # (which can be wrong — Figure 6.18 visually in solution
                # was tagged body_type=solution correctly, but Figure 6.16
                # in question stem was sometimes mis-tagged as solution).
                # The placeholder location is ground truth from the PDF.
                if forced_target is not None:
                    body_target = forced_target
                else:
                    body_target = (
                        "solution"
                        if getattr(fig, "body_type", None) == "solution"
                        else "question"
                    )
                target_text = (
                    q.solution_text if body_target == "solution" else q.raw_text
                ) or ""

                # Step 3: find position inside target text.
                # forced_offset from PATH 0 wins — it's the exact placeholder
                # location, captured during the same search that picked the
                # question. Skips the re-scan below.
                offset: int | None = forced_offset
                if offset is None and label_pattern:
                    offset = _find_inline_char_offset(target_text, label_pattern)
                # Anchor_text fallback ONLY when forced_target is None
                # (i.e., legacy / unmatched cases). For the structured
                # unlabelled path (forced_target set), we APPEND at the
                # end of the right body — per user spec: section_type +
                # question_no + body_type → append. No anchor search.
                if offset is None and forced_target is None:
                    anchor = (pos_meta.get("anchor_text") or "").strip()
                    if anchor and target_text:
                        # Try strict substring (60-char window, then 30).
                        for window in (60, 30):
                            needle = anchor[:window]
                            if not needle:
                                break
                            idx = target_text.find(needle)
                            if idx >= 0:
                                offset = idx
                                break

                placement_kind = "inline" if offset is not None else "appended"
                placement_char_offset = (
                    offset if offset is not None else len(target_text)
                )

                new_refs.append(FigureReference(
                    figure_id=fig.id, book_id=book_id,
                    section_ref=(q.section_ref or sec_slug or ""),
                    context="question", question_id=q.id,
                    placeholder_text=None, link_method="auto",
                    placement_kind=placement_kind,
                    placement_block_idx=None,
                    placement_char_offset=placement_char_offset,
                    # Explicit body target — frontend reads this directly
                    # instead of inferring from char_offset / placeholder
                    # text. Set from forced_target (PATH 0 placeholder match)
                    # or body_type (Gemini's classification fallback).
                    body_target=body_target,
                ))
                counters[
                    "question_inline" if offset is not None else "question_appended"
                ] += 1
                continue

            # ═══════════════════════════════════════════════════════
            # ctx = theory  →  find block in section, position relative
            # ═══════════════════════════════════════════════════════
            if sec_row is None:
                new_refs.append(_emit_unattached(
                    fig, sec_slug, "section_not_found", ctx="theory",
                ))
                continue

            blocks = sec_row.blocks or []
            block_idx: int | None = None
            placement_kind = "page_fallback"
            target_section_slug = sec_slug

            # Step 1a: labelled — try same-section label match first
            if normalized_label and isinstance(blocks, list) and blocks:
                block_idx = _find_inline_block_index(
                    blocks, normalized_label, label_pattern,
                )
                if block_idx is not None:
                    placement_kind = "inline"
                else:
                    # Step 1b: cross-section label index rescue
                    candidates = label_index.get(normalized_label, [])
                    picked = _pick_label_match(
                        candidates, fig.page_number, sections_by_id,
                    )
                    if picked:
                        cross_sid, cross_idx = picked
                        cross_sec = sections_by_id.get(cross_sid)
                        if cross_sec is not None:
                            target_section_slug = cross_sid
                            sec_row = cross_sec
                            block_idx = cross_idx
                            placement_kind = (
                                "inline" if cross_sid == sec_slug
                                else "label_crosssection"
                            )
                            if cross_sid != sec_slug:
                                counters["theory_relinked_by_label"] += 1

            # Step 1c: unlabelled — anchor_text substring match using the
            # LaTeX-aware normalizer (figure_normalizer.normalize_for_match).
            # Anchors arrive as rendered prose ("∠AOB is acute" with
            # Unicode glyphs); theory blocks store LaTeX source
            # ("$\\angle AOB$ is acute"). Both sides through the same
            # pylatexenc-based canonicalizer ("angle aob is acute") so
            # math-heavy anchors match reliably. Without this, every
            # anchor with Greek/math/comparison symbols falls through to
            # page_fallback (this was the silent driver of the 685
            # page_fallback refs across all books).
            if block_idx is None:
                anchor = (pos_meta.get("anchor_text") or "").strip()
                if anchor and isinstance(blocks, list):
                    from app.services.figure_normalizer import normalize_for_match
                    norm_anchor = normalize_for_match(anchor)
                    # Prefix windows PLUS exact sliding windows across the
                    # whole anchor — so a clean run of the anchor text still
                    # matches even when the START contains math/symbols that
                    # canonicalize differently (e.g. "tan⁻¹(b/a)" vs LaTeX).
                    # All needles are exact substrings → no false positives.
                    needles = _anchor_match_needles(norm_anchor)
                    if needles[0]:
                        # Pre-normalize every block's full text pool ONCE
                        # per section (per-figure was redundant work).
                        # Covers EVERY block type: body, heading, equation
                        # (single + multi-line), definition (term + content),
                        # key_point, figure (label + caption), list_item,
                        # table (caption + headers + rows), example_ref /
                        # exercise_ref / question_ref (label + number).
                        normalized_blocks: list[str] = [
                            normalize_for_match(_block_text_pool(b))
                            for b in blocks
                        ]
                        for needle in needles:
                            if not needle:
                                continue
                            for i, npool in enumerate(normalized_blocks):
                                if needle in npool:
                                    block_idx = i
                                    placement_kind = "inline"
                                    break
                            if block_idx is not None:
                                break

            # Step 1c-cross: anchor not found in the scheduled section →
            # search EVERY OTHER section for the SAME exact anchor text and
            # relocate the figure there. The figure pass sometimes files a
            # figure under the wrong sibling/sub-section; the anchor text is
            # the source of truth for where it belongs. Only STRONG (>=40-char)
            # exact-substring needles run cross-section, so a short generic
            # run can't pull a figure into an unrelated section.
            if block_idx is None:
                _xa = (pos_meta.get("anchor_text") or "").strip()
                if _xa:
                    from app.services.figure_normalizer import normalize_for_match
                    _xstrong = [
                        n for n in _anchor_match_needles(normalize_for_match(_xa))
                        if len(n) >= 40
                    ]
                    if _xstrong:
                        for _xsid, _xsec in sections_by_id.items():
                            if _xsid == target_section_slug:
                                continue
                            _xbl = getattr(_xsec, "blocks", None) or []
                            if not isinstance(_xbl, list) or not _xbl:
                                continue
                            _xpool = [
                                normalize_for_match(_block_text_pool(b)) for b in _xbl
                            ]
                            _xhit = None
                            for _nd in _xstrong:
                                for _bi, _p in enumerate(_xpool):
                                    if _nd in _p:
                                        _xhit = _bi
                                        break
                                if _xhit is not None:
                                    break
                            if _xhit is not None:
                                target_section_slug = _xsid
                                sec_row = _xsec
                                blocks = _xbl
                                block_idx = _xhit
                                placement_kind = "inline"
                                counters["theory_relinked_by_anchor"] = (
                                    counters.get("theory_relinked_by_anchor", 0) + 1
                                )
                                break

            # Step 1d: SUB-UNIT (list item) resolution. If the matched block
            # is a multi-item list, find WHICH item the anchor matched so the
            # figure lands INSIDE the list at that item — not stacked after
            # the whole merged list. We record a char offset into the
            # "\n"-joined items; the theory reader splits the list there.
            # Only theory list blocks set this; the question + export readers
            # consume char_offset BY CONTEXT, so theory values are isolated.
            sub_char_offset = None
            if block_idx is not None and isinstance(blocks, list):
                _blk = blocks[block_idx]
                _items = _blk.get("items") if isinstance(_blk, dict) else None
                if isinstance(_items, list) and len(_items) > 1:
                    from app.services.figure_normalizer import normalize_for_match
                    _anchor = (pos_meta.get("anchor_text") or "").strip()
                    _needles = _anchor_match_needles(normalize_for_match(_anchor))
                    _inorm = [normalize_for_match(str(it)) for it in _items]
                    _hit = None
                    for _nd in _needles:
                        if not _nd:
                            continue
                        for _k, _ip in enumerate(_inorm):
                            if _nd in _ip:
                                _hit = _k
                                break
                        if _hit is not None:
                            break
                    if _hit is not None:
                        _apos = (pos_meta.get("anchor_position") or "below").lower()
                        # "above" (text above figure) → figure AFTER that item;
                        # "below"/other → figure BEFORE that item.
                        _upto = _hit + 1 if _apos == "above" else _hit
                        sub_char_offset = len(
                            "\n".join(str(x) for x in _items[:_upto])
                        )

            # Step 2: positional adjustment for unlabelled.
            # anchor_position = where the ANCHOR TEXT sits relative to the
            # FIGURE (set by the figure-extraction pass), so INVERT it to
            # place the figure. The reader renders a figure right AFTER
            # block `placement_block_idx`, so:
            #   "above" (text above image) → figure BELOW the text →
            #            render AFTER the anchor block         (block_idx)
            #   "below" (text below image) → figure ABOVE the text →
            #            render BEFORE the anchor block         (block_idx - 1)
            #   "beside"/unknown → before the anchor (figure first, then text)
            # Matches the labelled/migrate path (Spot 2) convention below.
            if block_idx is not None:
                if sub_char_offset is not None:
                    # Sub-unit placement: keep the figure ON the list block;
                    # the char offset positions it at the right item inside.
                    final_block_idx = block_idx
                else:
                    anchor_position = (pos_meta.get("anchor_position") or "below").lower()
                    if anchor_position == "above":
                        final_block_idx = block_idx
                    else:  # below / beside / unknown → BEFORE the anchor block
                        final_block_idx = max(0, block_idx - 1)
            else:
                # Step 3: page_fallback — emit at section end so the
                # figure surfaces SOMEWHERE in its section instead of
                # being lost in the unattached tray.
                final_block_idx = None
                placement_kind = "page_fallback"
                counters["theory_appended"] += 1

            new_refs.append(FigureReference(
                figure_id=fig.id, book_id=book_id,
                section_ref=target_section_slug or fig.section_id or "",
                context="theory", question_id=None,
                placeholder_text=None, link_method="auto",
                placement_kind=placement_kind,
                placement_block_idx=final_block_idx,
                placement_char_offset=sub_char_offset,
            ))
            if placement_kind != "page_fallback":
                counters["theory_inline"] += 1

        except Exception as e:
            # Per-figure try/except — debt #43 observability + zero-drop
            # invariant. One bad figure shouldn't crash the whole book's
            # embedder run. Log + emit unattached so the figure isn't
            # completely lost from the DB.
            logger.exception(
                "figure_embedder: per-figure exception on figure=%s: %s",
                getattr(fig, "id", "?"), e,
            )
            counters["failed_exception"] += 1
            failure_log.append({
                "figure_id": str(getattr(fig, "id", "?")),
                "reason": "exception",
                "error": str(e)[:200],
            })
            try:
                new_refs.append(FigureReference(
                    figure_id=fig.id, book_id=book_id,
                    section_ref=getattr(fig, "section_id", "") or "",
                    context=(fig.context_hint or "theory").lower(),
                    question_id=None,
                    placeholder_text=None, link_method="auto",
                    placement_kind="unattached",
                    placement_block_idx=None,
                    placement_char_offset=None,
                ))
                counters["unattached"] += 1
            except Exception:
                pass  # truly broken figure; counter records it

    # Stash the failure log for caller-side logging / persistence.
    counters["_failure_log_count"] = len(failure_log)
    if failure_log:
        # Log first 10 for visibility without log spam
        logger.info(
            "figure_embedder: %d figures could not be placed cleanly. "
            "First failures: %s",
            len(failure_log), failure_log[:10],
        )
    return new_refs, counters


# ─── LEGACY PLACEMENT (kept for reference / fallback) ─────────────
# Replaced by the top-down v2 above. Kept here in case a regression
# needs to compare behavior; will be deleted after one session of
# verifying the new function on real uploads.

def _compute_figure_placements_legacy(
    figures,
    sections_by_id,
    questions,
    questions_by_section,
    label_index,
    book_id: UUID,
):
    """Walk every figure and decide where each FigureReference row
    should land. Returns (refs_to_insert, counters_dict). No DB I/O.

    Two passes per figure:

    Pass 2 (UNLABELLED) — figures whose regen_meta carries
    is_labelled=False were extracted without a "Figure X.Y" caption.
    Placement strategy:
      1. Resolve target section via fig.section_id or page_number → section
      2. context=question + question_no → attach to the matching
         question (searched globally; question_number is unique per
         book) at the END of question.raw_text
      3. context=theory + anchor_text → fuzzy-match the anchor against
         section.blocks[].c (60-char snippet, 30-char fallback);
         anchor_position="above" inserts BEFORE the matched block;
         "below"/"beside" inserts AFTER
      4. Ultimate fallback: section end (placement_kind="page_fallback")
      5. No section resolvable: unattached tray

    Pass 1 (LABELLED) — figures with a "Figure X.Y" caption. Default
    path. Routing follows context_hint strictly:
      context="theory" → only placed in theory body
      context="question" → only placed beside a question
    Within each, the priority is:
      1. Global label match against text bodies (theory blocks or
         question raw_text + solution_text)
      2. Section fallback (figure's page-detected section_id)
      3. (Theory only) orphan page-range fallback for figures whose
         extraction anchor was lost
      4. Unattached tray
    """
    counters = {
        "figures_seen": 0,
        "theory_inline": 0,
        "theory_appended": 0,
        "question_inline": 0,
        "question_appended": 0,
        "unattached": 0,
        "skipped_no_section": 0,
        "theory_relinked_by_label": 0,
    }
    new_refs: list[FigureReference] = []

    for fig in figures:
        counters["figures_seen"] += 1
        section_id = fig.section_id or ""

        # ─── Pass 2: positional placement for UNLABELLED figures ──
        pos_meta = fig.regen_meta if isinstance(fig.regen_meta, dict) else None
        if pos_meta and pos_meta.get("is_labelled") is False:
            anchor_text = (pos_meta.get("anchor_text") or "").strip()
            anchor_position = (pos_meta.get("anchor_position") or "below").lower()
            question_no = (pos_meta.get("question_no") or "").strip()
            ctx = (fig.context_hint or "theory").lower()

            target_sid = section_id if section_id and section_id != "_orphan" else ""
            if (not target_sid or target_sid not in sections_by_id) and fig.page_number is not None:
                for sid_iter, sec_iter in sections_by_id.items():
                    ps = getattr(sec_iter, "page_start", None)
                    pe = getattr(sec_iter, "page_end", None)
                    if ps is not None and pe is not None and ps <= fig.page_number <= pe:
                        target_sid = sid_iter
                        break

            placed = False

            # Question path — global question_number lookup. The
            # figure's page-based section_id often does NOT match the
            # question's section_ref (e.g. EXAMPLE 6.13 is filed under
            # its own section "6-example-6.13" while the figure lands
            # under the surrounding theory section). question_number
            # is globally unique per book, so a global search is safe.
            #
            # Normalise both sides: strip surrounding parens / dots /
            # whitespace and lowercase. Handles Gemini emitting "Q.39"
            # / "(39)" / "39." / "Q39" while the question_number field
            # in DB carries just "39", and vice versa. Without this
            # the match fails on any cosmetic difference.
            import re as _qre

            def _norm_qno(s: str | None) -> str:
                if not s:
                    return ""
                t = s.strip().lower()
                # Drop a leading "Q" prefix ("q.39", "q39", "q 39")
                t = _qre.sub(r"^q\.?\s*", "", t)
                # Drop wrapping parens / brackets / dots
                t = t.strip("().[]{} \t.")
                return t

            if ctx == "question" and question_no:
                want = _norm_qno(question_no)
                if want:
                    for q in questions:
                        if _norm_qno(q.question_number) == want:
                            q_ctx, q_off = _question_body_target(fig, q)
                            new_refs.append(FigureReference(
                                figure_id=fig.id, book_id=book_id,
                                section_ref=(q.section_ref or target_sid),
                                context=q_ctx, question_id=q.id,
                                placeholder_text=None, link_method="auto",
                                placement_kind="inline", placement_block_idx=None,
                                placement_char_offset=q_off,
                            ))
                            counters["question_inline"] += 1
                            placed = True
                            break

            # NEW: section_ref pattern fallback.
            # When question_no path didn't match by question_number (a
            # 9-question silent-drop pattern observed in prod: the
            # question worker created 24 example-section rows but only
            # populated question_number on 15 of them — the 9
            # figure-bearing examples got empty question_number even
            # though their section_ref like "9-construction-of-triangles-6-example-9.11"
            # encodes the number). Match figure.question_no against the
            # section_ref's "-example-X.Y" suffix or "-X.Y" tail.
            # Strict: question_no must be at the END of section_ref or
            # adjacent to "-example-" — avoids accidentally matching a
            # different section that just happens to contain "9.11"
            # somewhere in its slug.
            if not placed and ctx == "question" and question_no:
                want = _norm_qno(question_no)
                if want:
                    for q in questions:
                        sref = (q.section_ref or "").lower()
                        # Match patterns like "...-example-9.11", "...-9.11"
                        if (sref.endswith(f"-{want}")
                                or sref.endswith(f"-example-{want}")):
                            q_ctx, q_off = _question_body_target(fig, q)
                            new_refs.append(FigureReference(
                                figure_id=fig.id, book_id=book_id,
                                section_ref=(q.section_ref or target_sid),
                                context=q_ctx, question_id=q.id,
                                placeholder_text=None, link_method="auto",
                                placement_kind="inline",
                                placement_block_idx=None,
                                placement_char_offset=q_off,
                            ))
                            counters["question_inline"] += 1
                            placed = True
                            logger.info(
                                "figure_embedder: matched figure→question via section_ref pattern "
                                "(qno=%s, section_ref=%s)",
                                want, q.section_ref,
                            )
                            break

            # NEW: anchor_text → question.raw_text substring match.
            # Runs when ctx="question" AND either question_no is empty
            # (example sections often emit empty question_number) OR the
            # question_no lookup above didn't find a match. We try the
            # OTHER direction: is the figure's anchor_text a substring of
            # any question's raw_text? If so, that's the figure's question.
            # Handles two failure classes:
            #   - Empty question_number (Q9.6/Q9.11 example sections)
            #   - Mis-classified figure (Gemini labelled it question but
            #     used the question stem as anchor_text — common pattern)
            if not placed and ctx == "question" and (anchor_text or "").strip():
                # Need _norm_match defined earlier — pull it up here.
                # Simple normalisation: lowercase + whitespace-collapse.
                import re as _re_q
                _anchor_norm_q = _re_q.sub(
                    r"\s+", " ", (anchor_text or "").strip().lower()
                )
                if len(_anchor_norm_q) >= 20:  # avoid 1-2 word false matches
                    for q in questions:
                        if not q.raw_text:
                            continue
                        q_norm = _re_q.sub(
                            r"\s+", " ", q.raw_text.strip().lower()
                        )
                        if _anchor_norm_q in q_norm:
                            q_ctx, q_off = _question_body_target(fig, q)
                            new_refs.append(FigureReference(
                                figure_id=fig.id, book_id=book_id,
                                section_ref=(q.section_ref or target_sid),
                                context=q_ctx, question_id=q.id,
                                placeholder_text=None, link_method="auto",
                                placement_kind="inline", placement_block_idx=None,
                                placement_char_offset=q_off,
                            ))
                            counters["question_inline"] += 1
                            placed = True
                            break
            if placed:
                continue

            # Theory path — STRICT full-anchor substring match.
            #
            # The prompt instructs Gemini to emit anchor_text as the
            # complete verbatim OCR wording of the printed sentence
            # adjacent to the image (no paraphrase, no truncation, no
            # length cap). Block.c contains the same OCR of the same
            # printed text. So a 100% substring of the normalised
            # anchor_text in the normalised block.c is the strong
            # signal that THIS block is the right anchor.
            #
            # We deliberately do NOT fall back to a shorter fuzzy
            # window. A wrong-position match (figure pinned to the
            # wrong block because a 30-char prefix happened to appear
            # somewhere else) is worse than the page_fallback below
            # — the user can spot a section-end figure and reposition,
            # but a silently-misplaced inline figure looks intentional.
            #
            # Normalisation kept minimal:
            #   - lowercase
            #   - collapse runs of whitespace to a single space
            # We deliberately keep punctuation and math symbols
            # (∠, ≤, π, etc.) — those are part of the anchor identity
            # and a real OCR-to-OCR match preserves them.
            import re as _re
            # Anchor-text fallback. Runs for theory figures AND for
            # question figures whose question_no didn't resolve to a DB
            # question above (typo, OCR drift, duplicate-question dedup).
            # Better to surface the image at the right block in the
            # section than to silently drop it.
            if anchor_text:
                # Subscript / superscript digits (and a few math letters)
                # commonly drift between Gemini passes — the figure
                # extractor may transcribe "l1, l2" while the theory
                # extractor uses Unicode "l₁, l₂". Both refer to the
                # same printed glyph. Normalising before substring match
                # preserves accuracy without weakening to fuzzy logic.
                _SUB_SUP_TR = str.maketrans({
                    "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4",
                    "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9",
                    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
                    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
                    "₊": "+", "₋": "-", "⁺": "+", "⁻": "-",
                    "ₓ": "x", "ⁿ": "n",
                    # Math-glyph OCR confusion. Theory extractor sometimes
                    # transcribes the angle glyph ∠ as a capital Z (slab-
                    # serif visual similarity). Figure extractor reads ∠
                    # correctly. Map ∠ → z so anchors containing "∠AOC"
                    # match blocks containing "ZAOC" once both sides are
                    # lowercased. One-direction only (Z stays Z) — never
                    # broaden Z-as-letter to match ∠-as-glyph, that would
                    # false-match prose containing words like "Zone".
                    "∠": "z",
                    # Parallel-lines glyph. Theory OCR commonly writes it
                    # as "||" (two pipes). Map ∥ → "||" so anchors like
                    # "l₁ ∥ l₂" match blocks like "l1 || l2" after
                    # normalisation. Multi-char mapping is supported by
                    # str.maketrans + translate.
                    "∥": "||",
                    # Triangle glyph confusions. Theory OCR commonly
                    # transcribes the triangle ∆ (U+2206 INCREMENT) or
                    # Δ (U+0394 GREEK CAPITAL LETTER DELTA) as a plain
                    # capital "A" (visual similarity in sans-serif
                    # fonts). Figure extractor reads the glyph
                    # correctly. Map both glyphs → "A" so anchors with
                    # "∆ABC" match blocks containing "AABC" once both
                    # sides are lowercased. Risk profile mirrors the
                    # ∠→z mapping: false positives possible only when a
                    # block legitimately contains "A" before the same
                    # 3-letter sequence that follows the anchor's
                    # triangle glyph, AND the full anchor sentence
                    # also substring-matches — very unlikely outside
                    # the math-prose case this targets.
                    "∆": "A",
                    "Δ": "A",
                })

                def _norm_match(text: str) -> str:
                    """Lowercase + collapse whitespace + flatten
                    subscript/superscript digits to ASCII. Keeps math
                    symbols (∠, ≤, π, etc.) and punctuation intact so the
                    substring check remains identity-preserving — we only
                    smooth over OCR-pass differences."""
                    if not text:
                        return ""
                    t = text.translate(_SUB_SUP_TR).lower()
                    return _re.sub(r"\s+", " ", t).strip()

                anchor_norm = _norm_match(anchor_text)

                # Anchor text is the PRIMARY signal for placement — page
                # assignment is just an initial hint. The linker sometimes
                # picks the wrong section when multiple sections share a
                # page (e.g. "5-introduction" page 3-3 and "5-basic-
                # concepts" page 3-6 both cover page 3; linker takes the
                # first → fig assigned to 5-introduction but the anchor
                # actually lives in 5-basic-concepts).
                #
                # Strategy: try the originally-assigned target_sid first
                # (cheap, usually correct), then expand to ANY section
                # whose page range contains the figure's page. First
                # 100%-substring match wins and the figure migrates to
                # that section. Still strict — wrong placement is worse
                # than section-end fallback.
                def _block_candidates(b: dict) -> list[str]:
                    """Return the normalised text(s) to try matching the
                    anchor against. For `def` blocks the candidate is
                    `term + ": " + c` (the printed form often combines
                    them) AND `c` alone (some def blocks are body-only).
                    For `list` blocks each `items[i]` string is its own
                    candidate (the text lives in items[], not c). Other
                    block types fall through to just `c`."""
                    out: list[str] = []
                    c = b.get("c") or ""
                    term = b.get("term") or ""
                    if b.get("t") == "def" and term:
                        out.append(_norm_match(f"{term}: {c}"))
                    if c:
                        out.append(_norm_match(c))
                    if b.get("t") == "list":
                        for it in (b.get("items") or []):
                            if isinstance(it, str) and it:
                                out.append(_norm_match(it))
                    if b.get("t") == "example":
                        # `prob` carries the problem statement — exactly
                        # what Gemini would quote as anchor_text when a
                        # figure sits next to a worked example.
                        prob = b.get("prob") or ""
                        if prob:
                            out.append(_norm_match(prob))
                    return [s for s in out if s]

                def _match_in_section(sid: str) -> int | None:
                    """Strict full-anchor substring match against blocks
                    of section ``sid``. Returns matched block index or None."""
                    sec_row = sections_by_id.get(sid)
                    if not sec_row:
                        return None
                    blocks = (sec_row.blocks if sec_row else None) or []
                    if not anchor_norm:
                        return None
                    for idx, b in enumerate(blocks):
                        if not isinstance(b, dict):
                            continue
                        for cand in _block_candidates(b):
                            if anchor_norm in cand:
                                return idx
                    return None

                matched_sid: str | None = None
                matched_idx: int | None = None

                # 1. Try the initially-assigned target_sid first.
                if target_sid:
                    idx = _match_in_section(target_sid)
                    if idx is not None:
                        matched_sid = target_sid
                        matched_idx = idx

                # 2. If no match in target_sid, scan other sections whose
                # page range includes the figure's page. Anchor text is
                # the source of truth — page assignment was just a hint.
                if matched_idx is None and fig.page_number is not None:
                    page = fig.page_number
                    for sid_iter, sec_iter in sections_by_id.items():
                        if sid_iter == target_sid:
                            continue  # already tried
                        ps = getattr(sec_iter, "page_start", None)
                        pe = getattr(sec_iter, "page_end", None)
                        if ps is None or pe is None:
                            continue
                        if not (ps <= page <= pe):
                            continue
                        idx = _match_in_section(sid_iter)
                        if idx is not None:
                            matched_sid = sid_iter
                            matched_idx = idx
                            break

                # 3. Last-resort full anchor scan ignoring page hint.
                # Catches schema bugs where a leaf section's page_end
                # didn't grow with its extracted block content (e.g.
                # blocks span pages 3-5 but page_end=3), leaving figures
                # on the un-covered pages with no page-overlapping leaf
                # to migrate to. Anchor_text is a full-sentence strict
                # substring match — extremely unlikely to false-match
                # across sections, so safe to widen the scan.
                if matched_idx is None and anchor_norm:
                    tried = {target_sid} if target_sid else set()
                    for sid_iter in sections_by_id:
                        if sid_iter in tried:
                            continue
                        idx = _match_in_section(sid_iter)
                        if idx is not None:
                            matched_sid = sid_iter
                            matched_idx = idx
                            break

                # NOTE: a 4th-pass fuzzy fallback was considered and
                # rejected. Token-containment / jaccard cannot reliably
                # distinguish a genuine paraphrase (anchor adds 2-3
                # words) from a same-language false positive (both
                # contain common stopwords). A silently-mis-placed
                # figure that LOOKS correct is worse than a section-end
                # page_fallback the user can spot and reposition. The
                # right answer for true paraphrase cases is Task #19
                # (one Gemini verification call: "of these N candidate
                # blocks, which one does this anchor refer to?") —
                # cheaper and more accurate than text heuristics.

                if matched_sid is not None and matched_idx is not None:
                    # anchor_position semantics — relative to where the
                    # printed anchor sentence sits with respect to the
                    # image on the original page:
                    #   "above"  → anchor sits ABOVE the image (image is
                    #              below the anchor) → figure renders
                    #              right AFTER the anchor block
                    #   "below"  → anchor sits BELOW the image (image is
                    #              above the anchor) → figure renders
                    #              right BEFORE the anchor block, i.e.
                    #              after the previous block
                    #   "beside" → anchor is at the same vertical level
                    #              as the image; conventionally we put
                    #              the image just BEFORE the anchor so
                    #              the reader sees the figure first,
                    #              then the descriptive sentence
                    # seed_draft_items_from_merge emits figures with
                    # placement_block_idx=i AFTER block i.
                    if anchor_position == "above":
                        placement_idx = matched_idx
                    elif anchor_position == "below":
                        placement_idx = max(0, matched_idx - 1)
                    else:  # "beside" or unknown
                        placement_idx = max(0, matched_idx - 1)
                    new_refs.append(FigureReference(
                        figure_id=fig.id, book_id=book_id,
                        section_ref=matched_sid,   # MIGRATE to the section where anchor was found
                        context="theory", question_id=None,
                        placeholder_text=None, link_method="auto",
                        placement_kind="inline",
                        placement_block_idx=placement_idx,
                        placement_char_offset=None,
                    ))
                    counters["theory_inline"] += 1
                    placed = True
            if placed:
                continue

            # Ultimate fallback — section end. Always emit as
            # context="theory" page_fallback so the renderer surfaces it
            # at the section tail. Previously a ctx="question" figure
            # that missed its question_no match landed here with
            # context="question" + question_id=None, which final_merge
            # silently dropped (it requires question_id to attach a
            # question figure). Routing through theory keeps the image
            # visible while preserving section context.
            if target_sid:
                new_refs.append(FigureReference(
                    figure_id=fig.id, book_id=book_id,
                    section_ref=target_sid,
                    context="theory",
                    question_id=None,
                    placeholder_text=None, link_method="auto",
                    placement_kind="page_fallback",
                    placement_block_idx=None,
                    placement_char_offset=None,
                ))
                counters["theory_appended"] += 1
                continue

            # No section at all → unattached tray
            new_refs.append(FigureReference(
                figure_id=fig.id, book_id=book_id, section_ref=section_id,
                context="theory", question_id=None,
                placeholder_text=None, link_method="auto",
                placement_kind="unattached", placement_block_idx=None,
                placement_char_offset=None,
            ))
            counters["unattached"] += 1
            continue

        # ─── Pass 1: label-first placement for LABELLED figures ──
        orphan = (not section_id or section_id == "_orphan")
        label_norm = (fig.normalized_label or _normalize_label(fig.figure_number) or "").strip()
        label_pattern = _build_label_pattern(label_norm)
        context = (fig.context_hint or "theory").lower()
        target_kind = "question" if context == "question" else "theory"

        if target_kind == "theory":
            # Label-first global match against theory blocks
            label_match = None
            if label_norm:
                cands = label_index.get(label_norm) or []
                label_match = _pick_label_match(cands, fig.page_number, sections_by_id)
            if label_match is not None:
                matched_sec_id, matched_block_idx = label_match
                new_refs.append(FigureReference(
                    figure_id=fig.id, book_id=book_id,
                    section_ref=matched_sec_id,
                    context="theory", question_id=None,
                    placeholder_text=fig.figure_number, link_method="auto",
                    placement_kind="inline",
                    placement_block_idx=matched_block_idx,
                    placement_char_offset=None,
                ))
                counters["theory_inline"] += 1
                continue

            # Section fallback — figure's page-detected section_id
            if section_id and section_id in sections_by_id:
                new_refs.append(FigureReference(
                    figure_id=fig.id, book_id=book_id, section_ref=section_id,
                    context="theory", question_id=None,
                    placeholder_text=fig.figure_number, link_method="auto",
                    placement_kind="page_fallback", placement_block_idx=None,
                    placement_char_offset=None,
                ))
                counters["theory_appended"] += 1
                continue

            # Orphan page-range fallback
            if orphan and fig.page_number is not None:
                page = fig.page_number
                matched_section_id = None
                for sid_iter, sec_iter in sections_by_id.items():
                    ps = getattr(sec_iter, "page_start", None)
                    pe = getattr(sec_iter, "page_end", None)
                    if ps is not None and pe is not None and ps <= page <= pe:
                        matched_section_id = sid_iter
                        break
                if matched_section_id:
                    new_refs.append(FigureReference(
                        figure_id=fig.id, book_id=book_id,
                        section_ref=matched_section_id,
                        context="theory", question_id=None,
                        placeholder_text=fig.figure_number, link_method="auto",
                        placement_kind="page_fallback", placement_block_idx=None,
                        placement_char_offset=None,
                    ))
                    counters["theory_appended"] += 1
                    continue

            # No match anywhere → unattached
            new_refs.append(FigureReference(
                figure_id=fig.id, book_id=book_id, section_ref=section_id,
                context="theory", question_id=None,
                placeholder_text=fig.figure_number, link_method="auto",
                placement_kind="unattached", placement_block_idx=None,
                placement_char_offset=None,
            ))
            counters["unattached"] += 1
            continue

        # target_kind == "question"
        # Label-first global match in question text. F1: route by
        # body_type — solution figs search solution_text only (and land
        # with context="solution"), all others search raw_text first
        # then fall back to combined (legacy behaviour).
        if label_norm and label_pattern is not None:
            body_type = getattr(fig, "body_type", None)
            global_hit = None
            for q in questions:
                if body_type == "solution":
                    # F1 — solution figure: only match in solution_text
                    sol = getattr(q, "solution_text", "") or ""
                    offset = _find_inline_char_offset(sol, label_pattern)
                    if offset is not None:
                        global_hit = (q, offset, "solution")
                        break
                else:
                    # body_type == "question" OR legacy (None) — search
                    # raw_text first, then combined for backward compat.
                    raw = q.raw_text or ""
                    offset = _find_inline_char_offset(raw, label_pattern)
                    if offset is not None:
                        global_hit = (q, offset, "question")
                        break
                    if body_type is None:
                        combined = raw + "\n" + (
                            getattr(q, "solution_text", "") or ""
                        )
                        offset = _find_inline_char_offset(combined, label_pattern)
                        if offset is not None:
                            global_hit = (q, offset, "question")
                            break
            if global_hit is not None:
                q, offset, ctx_out = global_hit
                resolved_sec = q.section_ref or section_id
                new_refs.append(FigureReference(
                    figure_id=fig.id, book_id=book_id,
                    section_ref=resolved_sec,
                    context=ctx_out, question_id=q.id,
                    placeholder_text=fig.figure_number, link_method="auto",
                    placement_kind="inline", placement_block_idx=None,
                    placement_char_offset=offset,
                ))
                counters["question_inline"] += 1
                continue

        # Section fallback (question context) — appended as theory
        if section_id and section_id in sections_by_id:
            new_refs.append(FigureReference(
                figure_id=fig.id, book_id=book_id, section_ref=section_id,
                context="theory", question_id=None,
                placeholder_text=fig.figure_number, link_method="auto",
                placement_kind="page_fallback", placement_block_idx=None,
                placement_char_offset=None,
            ))
            counters["theory_appended"] += 1
            continue

        # Unattached
        new_refs.append(FigureReference(
            figure_id=fig.id, book_id=book_id, section_ref=section_id,
            context="question", question_id=None,
            placeholder_text=fig.figure_number, link_method="auto",
            placement_kind="unattached", placement_block_idx=None,
            placement_char_offset=None,
        ))
        counters["unattached"] += 1

    return new_refs, counters


# ─── ASYNC WRAPPER ────────────────────────────────────────────────
async def embed_figures_for_book(
    session: AsyncSession,
    book_id: UUID,
) -> dict[str, int]:
    """Walk every Figure for this book, compute its placement, and write
    the result back to ``figure_references``. Idempotent: rebuilds all
    placement rows from scratch so re-running after schema edits or new
    regen variants produces a consistent state. All placement decisions
    live in _compute_figure_placements() — this function is just the
    async DB I/O wrapper.
    """
    sections = (
        await session.execute(
            select(Section).where(Section.book_id == book_id)
        )
    ).scalars().all()
    sections_by_id = {s.section_id: s for s in sections}

    # Restrict to latest ready bank — older banks have stale question_ids
    # that newer extractions replace.
    from app.models.question_bank import QuestionBank
    # Accept both "ready" and "partial" — a partial bank still has
    # questions in the DB, and final_merge surfaces them. Restricting to
    # "ready" only would silently drop every question-attached unlabelled
    # figure when even one section's question worker failed.
    latest_bank = (
        await session.execute(
            select(QuestionBank)
            .where(QuestionBank.book_id == book_id)
            .where(QuestionBank.status.in_(["ready", "partial"]))
            .order_by(QuestionBank.created_at.desc())
            .limit(1)
        )
    ).scalars().first()
    questions: list[Question] = []
    if latest_bank is not None:
        questions = (
            await session.execute(
                select(Question)
                .where(Question.book_id == book_id)
                .where(Question.bank_id == latest_bank.id)
                .where(Question.regen_id.is_(None))
            )
        ).scalars().all()
    questions_by_section: dict[str, list[Question]] = {}
    for q in questions:
        if q.section_ref:
            questions_by_section.setdefault(q.section_ref, []).append(q)

    figures = (
        await session.execute(
            select(Figure).where(Figure.book_id == book_id)
        )
    ).scalars().all()

    # Load existing refs FIRST so we can diff against the target. Replaces
    # the old wipe-and-rebuild — see debt #44 + _apply_placement_diff.
    existing_refs = (
        await session.execute(
            select(FigureReference).where(FigureReference.book_id == book_id)
        )
    ).scalars().all()

    label_index = _build_global_label_index(sections)

    refs, counters = _compute_figure_placements(
        figures, sections_by_id, questions,
        questions_by_section, label_index, book_id,
    )

    # Phase 2 of canonical identity migration (CONTRACT.md §1):
    # stamp every TARGET FigureReference with the canonical section UUID.
    # Resolved via the in-memory sections_by_id map (no extra DB I/O).
    section_uuid_by_slug = {slug: sec.id for slug, sec in sections_by_id.items()}
    question_uuid_by_id = {q.id: q.section_uuid for q in questions if q.section_uuid}
    for ref in refs:
        if ref.question_id and ref.question_id in question_uuid_by_id:
            ref.section_uuid = question_uuid_by_id[ref.question_id]
        elif ref.section_ref and ref.section_ref in section_uuid_by_slug:
            ref.section_uuid = section_uuid_by_slug[ref.section_ref]
        # else: leave NULL; Phase 4 reader treats this as "unlinked"

    # ── DIFF / UPSERT (debt #44) ─────────────────────────────────
    # Apply only the deltas — preserves user manual placements
    # (link_method='manual'), is_hidden flags, and identity (id).
    to_insert, to_update, to_delete = _apply_placement_diff(existing_refs, refs)
    for ref in to_insert:
        session.add(ref)
    for existing, target in to_update:
        _apply_update(existing, target)
    for ref in to_delete:
        await session.delete(ref)
    await session.flush()

    counters["diff_inserted"] = len(to_insert)
    counters["diff_updated"] = len(to_update)
    counters["diff_deleted"] = len(to_delete)
    counters["diff_preserved_manual"] = sum(
        1 for r in existing_refs if r.link_method == "manual"
    )
    logger.info("figure_embedder: book=%s %s", book_id, counters)
    return counters


# ─── SYNC WRAPPER ─────────────────────────────────────────────────
def embed_figures_for_book_sync(session, book_id: UUID) -> dict[str, int]:
    """Sync variant for the v3 worker (which runs in a sync SQLAlchemy
    session context). Identical behaviour to the async wrapper —
    same placement logic via _compute_figure_placements(), just sync
    DB I/O.
    """
    from sqlalchemy import delete as _delete, select as _select

    sections = session.execute(
        _select(Section).where(Section.book_id == book_id)
    ).scalars().all()
    sections_by_id = {s.section_id: s for s in sections}

    from app.models.question_bank import QuestionBank
    # Accept both "ready" and "partial" — mirrors final_merge (a partial
    # bank still has live questions; restricting to "ready" silently drops
    # every question-attached unlabelled figure on books where one
    # section's question worker failed).
    latest_bank = session.execute(
        _select(QuestionBank)
        .where(QuestionBank.book_id == book_id)
        .where(QuestionBank.status.in_(["ready", "partial"]))
        .order_by(QuestionBank.created_at.desc())
        .limit(1)
    ).scalars().first()
    questions: list[Question] = []
    if latest_bank is not None:
        questions = session.execute(
            _select(Question)
            .where(Question.book_id == book_id)
            .where(Question.bank_id == latest_bank.id)
            .where(Question.regen_id.is_(None))
        ).scalars().all()
    questions_by_section: dict[str, list[Question]] = {}
    for q in questions:
        if q.section_ref:
            questions_by_section.setdefault(q.section_ref, []).append(q)

    figures = session.execute(
        _select(Figure).where(Figure.book_id == book_id)
    ).scalars().all()

    # Load existing refs FIRST for diff (replaces wipe-and-rebuild — debt #44)
    existing_refs = session.execute(
        _select(FigureReference).where(FigureReference.book_id == book_id)
    ).scalars().all()

    label_index = _build_global_label_index(sections)

    refs, counters = _compute_figure_placements(
        figures, sections_by_id, questions,
        questions_by_section, label_index, book_id,
    )

    # Phase 2 of canonical identity migration (CONTRACT.md §1):
    # stamp every TARGET FigureReference with the canonical section UUID.
    # Same logic as the async wrapper above — kept inline to avoid
    # async/sync drift.
    section_uuid_by_slug = {slug: sec.id for slug, sec in sections_by_id.items()}
    question_uuid_by_id = {q.id: q.section_uuid for q in questions if q.section_uuid}
    for ref in refs:
        if ref.question_id and ref.question_id in question_uuid_by_id:
            ref.section_uuid = question_uuid_by_id[ref.question_id]
        elif ref.section_ref and ref.section_ref in section_uuid_by_slug:
            ref.section_uuid = section_uuid_by_slug[ref.section_ref]

    # ── DIFF / UPSERT (debt #44) ─────────────────────────────────
    # Apply only the deltas — preserves user manual placements
    # (link_method='manual'), is_hidden flags, and identity (id).
    to_insert, to_update, to_delete = _apply_placement_diff(existing_refs, refs)
    for ref in to_insert:
        session.add(ref)
    for existing, target in to_update:
        _apply_update(existing, target)
    for ref in to_delete:
        session.delete(ref)
    session.flush()

    counters["diff_inserted"] = len(to_insert)
    counters["diff_updated"] = len(to_update)
    counters["diff_deleted"] = len(to_delete)
    counters["diff_preserved_manual"] = sum(
        1 for r in existing_refs if r.link_method == "manual"
    )
    logger.info("figure_embedder (sync): book=%s %s", book_id, counters)
    return counters


__all__ = [
    "embed_figures_for_book",
    "embed_figures_for_book_sync",
    "_select_variant",
]
