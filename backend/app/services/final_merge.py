"""Final Merge — Phase 2.

Combines extracted theory blocks, question-bank questions, and embedded
figures into a single unified document, ordered by the book's schema.
Pure composition — no new AI calls, no new extraction. Reuses existing
embedder placement decisions.

Surface (returned dict shape):

    {
        "book": { "id", "title", "subject" },
        "sections": [
            {
                "section_id", "section_title", "depth",
                "blocks": [ {Block dict} ... ],
                "embedded_figures": [ {figure dict, theory context} ... ],
                "questions": [ {Question dict + embedded_figures} ... ]
            },
            ...
        ],
        "unattached_figures": [ ... ]  # informational: not in the merge
    }

The frontend renders this with the same components used by Reader (theory
blocks + embedded figures) and Questions tab (question cards + embedded
figures). Exporters consume the same dict to produce MD / DOCX.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.book import Book
from app.models.figure import Figure
from app.models.figure_reference import FigureReference
from app.models.question import Question
from app.models.question_bank import QuestionBank
from app.models.question_regeneration import QuestionRegeneration
from app.models.regeneration import Regeneration
from app.models.section import Section
from app.schemas.analyser import BookSchema
from app.services.chunk_builder import flatten_sections

logger = logging.getLogger(__name__)


def _extract_image_regen_hint(q: Question) -> dict[str, Any] | None:
    """Phase 4 — surface the multimodal regen verdict if present.
    Returns {"needed": True, "reason": "..."} when the regen LLM flagged
    that the attached image no longer matches the new question text,
    else None.
    """
    qc = getattr(q, "qc_local", None)
    if not isinstance(qc, dict):
        return None
    ir = qc.get("image_regen")
    if not isinstance(ir, dict) or not ir.get("needed"):
        return None
    return {"needed": True, "reason": ir.get("reason") or ""}


def _extract_regenerated_diagram(q: Question) -> dict[str, Any] | None:
    """Step 2 — surface the regenerated LaTeX/SVG diagram payload from qc_local.

    The Composer seeds its draft from these question dicts, so without this the
    Final/Export DOCX path can never embed the new diagram. With it present, the
    docx builder rasterizes ``svg_preview`` to PNG and embeds it IN PLACE OF the
    original figure (honoring fallback_to_original). None when there is no regen
    diagram on the question.
    """
    qc = getattr(q, "qc_local", None)
    if not isinstance(qc, dict):
        return None
    rd = qc.get("regenerated_diagram")
    if not isinstance(rd, dict):
        return None
    return {
        "fallback_to_original": bool(rd.get("fallback_to_original", False)),
        "subject": rd.get("subject") or "",
        "latex_code": rd.get("latex_code") or "",
        "svg_preview": rd.get("svg_preview") or "",
        "description": rd.get("description") or "",
    }


# ---------------------------------------------------------------------------
# Chip ↔ Question merge
# ---------------------------------------------------------------------------

import re as _re

_NUM_RE = _re.compile(r"(\d+(?:\.\d+)+|\d+)")


def _extract_number(s: str | None) -> str | None:
    """Pull the first 'X.Y[.Z…]' or bare integer out of a string. Used to
    match chip.label/number to question.exercise_ref/question_number."""
    if not s:
        return None
    m = _NUM_RE.search(str(s))
    return m.group(1) if m else None


# Sub-question parenthesised parts: "1(i)" → "1.1", "1(ii)" → "1.2", etc.
# Maps roman numeral suffixes to dotted-decimal sub-question index so a chip
# numbered "1(iii)" matches a question whose exercise_ref is "Exercise 1.3".
_ROMAN_MAP = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5,
    "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10,
    "xi": 11, "xii": 12, "xiii": 13, "xiv": 14, "xv": 15,
}
_ROMAN_SUFFIX_RE = _re.compile(r"(\d+)\s*[\(\[]\s*([ivx]+)\s*[\)\]]", _re.IGNORECASE)


def _normalise_subquestion(s: str | None) -> str | None:
    """If the string contains a `N(roman)` sub-question pattern, convert to
    `N.M` dotted form. Returns the normalised number string or None if no
    match. Used so chips like "Exercise 1(i)" align with questions like
    "Exercise 1.1".
    """
    if not s:
        return None
    m = _ROMAN_SUFFIX_RE.search(str(s))
    if not m:
        return None
    parent = m.group(1)
    roman_idx = _ROMAN_MAP.get(m.group(2).lower())
    if roman_idx is None:
        return None
    return f"{parent}.{roman_idx}"


def _chip_number(block: dict[str, Any]) -> str | None:
    t = block.get("t")
    if t not in ("example_ref", "exercise_ref", "question_ref"):
        return None
    # 1) Sub-question pattern (Exercise 1(iii) → 1.3) takes priority over
    #    bare-integer extraction so chips align with dotted question refs.
    for src in (block.get("label"), block.get("number")):
        sub = _normalise_subquestion(src)
        if sub:
            return sub
    # 2) Prefer the chip's explicit number, fall back to extracting from label.
    n = _extract_number(block.get("number")) or _extract_number(block.get("label"))
    if n:
        return n
    # 3) Last resort: extract from section_id suffix (e.g. "-example-4.18")
    return _extract_number(block.get("section_id"))


def _question_number(q: dict[str, Any]) -> str | None:
    """A question may carry the number in question_number, exercise_ref,
    or its own section_ref (worked-example subsections encode it in the id)."""
    return (
        _extract_number(q.get("question_number"))
        or _extract_number(q.get("exercise_ref"))
        or _extract_number((q.get("section_ref") or "").split("-")[-1])
        or _extract_number(q.get("section_ref"))
    )


_EXAMPLE_HEADING_RE = _re.compile(
    r"^\s*(?:worked\s+)?example\s+\d+(?:\.\d+)*",
    _re.IGNORECASE,
)


def _is_example_heading_block(block: dict[str, Any]) -> tuple[bool, str | None]:
    """Detect a block that opens a worked-example range. Returns
    (is_example_heading, extracted_number)."""
    if not isinstance(block, dict):
        return (False, None)
    t = block.get("t")
    if t == "example":
        # Native example block — number is in label
        num = _extract_number(block.get("label") or "")
        if num:
            return (True, num)
        return (False, None)
    if t in ("h2", "h3", "h4"):
        text = (block.get("c") or "").strip()
        if _EXAMPLE_HEADING_RE.match(text):
            return (True, _extract_number(text))
    return (False, None)


def _find_example_ranges(blocks: list[Any]) -> list[tuple[int, int, str]]:
    """Walk blocks linearly. For each EXAMPLE heading, return
    (start_idx, end_idx_exclusive, number). The range extends from the
    heading through subsequent blocks until we hit:
      - another non-SOLUTION heading
      - a Key Point (kp) block (usually a section-level transition)
      - end of blocks
    The "SOLUTION" sub-heading is treated as part of the example, not a
    range-terminator.
    """
    ranges: list[tuple[int, int, str]] = []
    i = 0
    n = len(blocks)
    while i < n:
        is_ex, num = _is_example_heading_block(blocks[i])
        if not is_ex or not num:
            i += 1
            continue
        j = i + 1
        while j < n:
            b = blocks[j]
            if not isinstance(b, dict):
                j += 1
                continue
            t = b.get("t")
            if t in ("h2", "h3", "h4"):
                txt = (b.get("c") or "").strip().lower()
                if txt == "solution":
                    j += 1
                    continue  # SOLUTION sub-heading is part of the example
                break  # any other heading ends the range
            if t == "kp":
                break  # Key Point boxes usually indicate a transition
            # Don't extend over another EXAMPLE heading either
            is_ex2, _ = _is_example_heading_block(b)
            if is_ex2:
                break
            j += 1
        ranges.append((i, j, num))
        i = j
    return ranges


def _drop_in_section_worked_examples(
    section: dict[str, Any],
) -> dict[str, Any]:
    """Find in-section worked-example block ranges and drop them when a
    matching question with solution_text exists in the same section. The
    question gets inlined at the dropped range's anchor position.

    Conservative — only fires when:
      (a) An EXAMPLE heading block (h3 "Example X.Y" / native example
          block) is detected by ``_find_example_ranges``.
      (b) A question (inlined or standalone) shares the same X.Y number.
      (c) That question has non-empty solution_text (otherwise blocks ARE
          the only copy of the solution; do not drop).

    Index remapping: figures + remaining inlined-question anchors are
    shifted to match the new (shorter) blocks list.
    """
    blocks = section.get("blocks") or []
    standalone = section.get("questions") or []
    inlined = section.get("inlined_questions_by_block_idx") or {}
    inlined_qs = [q for qs in inlined.values() for q in qs]
    all_qs = inlined_qs + standalone
    if not blocks or not all_qs:
        return section

    ranges = _find_example_ranges(blocks)
    if not ranges:
        return section

    q_by_num: dict[str, dict[str, Any]] = {}
    for q in all_qs:
        n = _question_number(q)
        if n and (q.get("solution_text") or "").strip():
            # First wins (deterministic; small chance of multiple Q with
            # same number, in which case any is fine for dedup purpose)
            q_by_num.setdefault(n, q)

    drops: list[tuple[int, int, str]] = [
        (s, e, num) for s, e, num in ranges if num in q_by_num
    ]
    if not drops:
        return section

    drop_set: set[int] = set()
    for s, e, _ in drops:
        for k in range(s, e):
            drop_set.add(k)

    new_blocks: list[Any] = []
    old_to_new: list[int | None] = []
    new_idx = -1
    for i, b in enumerate(blocks):
        if i in drop_set:
            old_to_new.append(None)
        else:
            new_idx += 1
            new_blocks.append(b)
            old_to_new.append(new_idx)

    # Remap figure indices
    figs = section.get("embedded_figures") or []
    new_figs: list[Any] = []
    for f in figs:
        idx = f.get("placement_block_idx")
        if idx is None:
            new_figs.append(f)
        elif 0 <= idx < len(old_to_new):
            mapped = old_to_new[idx]
            if mapped is None:
                new_figs.append({**f, "placement_block_idx": None})
            else:
                new_figs.append({**f, "placement_block_idx": mapped})
        else:
            new_figs.append(f)

    # Remap existing inlined-question anchors
    new_inlined: dict[str, list[dict[str, Any]]] = {}
    for k, qs in inlined.items():
        try:
            ki = int(k)
        except (TypeError, ValueError):
            continue
        if ki < 0:
            new_inlined.setdefault(str(ki), []).extend(qs)
            continue
        if 0 <= ki < len(old_to_new):
            mapped = old_to_new[ki]
            target = str(mapped) if mapped is not None else "-1"
            new_inlined.setdefault(target, []).extend(qs)
        else:
            new_inlined.setdefault(str(ki), []).extend(qs)

    # Add the matched questions at their dropped-range's anchor
    consumed_q_ids: set[str] = set()
    for s, e, num in drops:
        q = q_by_num[num]
        qid = str(q.get("id") or "")
        if qid in consumed_q_ids:
            continue
        consumed_q_ids.add(qid)
        # Find the last surviving block index BEFORE the drop range
        anchor_old = s - 1
        anchor_new_idx = -1
        while anchor_old >= 0:
            if anchor_old < len(old_to_new) and old_to_new[anchor_old] is not None:
                anchor_new_idx = old_to_new[anchor_old]  # type: ignore[assignment]
                break
            anchor_old -= 1
        anchor_key = str(anchor_new_idx) if anchor_new_idx >= 0 else "-1"
        new_inlined.setdefault(anchor_key, []).append(q)

    # Remove the matched questions from standalone (if they were there)
    new_standalone = [
        q for q in standalone if str(q.get("id") or "") not in consumed_q_ids
    ]
    # Also strip them from new_inlined positions where they came from
    # (they might have been in inlined originally; we just re-anchored them)
    if consumed_q_ids:
        scrubbed: dict[str, list[dict[str, Any]]] = {}
        for k, qs in new_inlined.items():
            kept = [
                q for q in qs if str(q.get("id") or "") not in consumed_q_ids
            ]
            if kept:
                scrubbed[k] = kept
        # Now re-add at the drop-range anchors (we lost them in the scrub)
        for s, e, num in drops:
            q = q_by_num[num]
            qid = str(q.get("id") or "")
            anchor_old = s - 1
            anchor_new_idx = -1
            while anchor_old >= 0:
                if (
                    anchor_old < len(old_to_new)
                    and old_to_new[anchor_old] is not None
                ):
                    anchor_new_idx = old_to_new[anchor_old]  # type: ignore[assignment]
                    break
                anchor_old -= 1
            anchor_key = str(anchor_new_idx) if anchor_new_idx >= 0 else "-1"
            scrubbed.setdefault(anchor_key, []).append(q)
        new_inlined = scrubbed

    out = dict(section)
    out["blocks"] = new_blocks
    out["embedded_figures"] = new_figs
    out["inlined_questions_by_block_idx"] = new_inlined
    out["questions"] = new_standalone
    return out


def _looks_like_worked_example_section(section: dict[str, Any]) -> bool:
    """Heuristic: is this a section that contains exactly one worked
    example? Used to decide whether the remaining theory blocks can be
    safely dropped after the chip↔question merge (since they're just the
    OCR'd solution that the question's solution_text already carries).
    Deterministic, pure string match — no AI."""
    sid = (section.get("section_id") or "").lower()
    title = (section.get("section_title") or "").lower()
    if "-example-" in sid or sid.startswith("example-"):
        return True
    if title.startswith("example ") or title.startswith("worked example"):
        return True
    return False


def _drop_solution_duplicate_blocks(section: dict[str, Any]) -> dict[str, Any]:
    """For worked-example sections, the section's blocks are typically
    just the OCR'd solution to a single worked example. If that section
    also has a question (inlined or standalone) carrying its own
    solution_text, the blocks are a duplicate of the question's solution.
    Drop them — the question card renders the solution cleanly with
    KaTeX.

    Deterministic rule (no AI): triggered ONLY when
      (a) section_id / title looks like a worked example, AND
      (b) the section has exactly one associated question, AND
      (c) that question has non-empty solution_text.

    The single-question constraint keeps the rule conservative — sections
    with multiple questions or mixed content are left untouched.
    """
    if not _looks_like_worked_example_section(section):
        return section
    inlined = section.get("inlined_questions_by_block_idx") or {}
    standalone = section.get("questions") or []
    inlined_qs: list[dict[str, Any]] = []
    for qs in inlined.values():
        inlined_qs.extend(qs)
    total_questions = len(inlined_qs) + len(standalone)
    if total_questions != 1:
        return section
    the_q = (inlined_qs or standalone)[0]
    if not (the_q.get("solution_text") or "").strip():
        return section
    out = dict(section)
    out["blocks"] = []
    # Preserve trailing figures only (placement_block_idx=None); drop the rest
    figs = section.get("embedded_figures") or []
    out["embedded_figures"] = [
        f for f in figs if f.get("placement_block_idx") is None
    ]
    # If the question was standalone (no chip-merge happened), move it to
    # the inlined anchor "-1" so the frontend renders it consistently.
    if not inlined_qs and standalone:
        out["inlined_questions_by_block_idx"] = {"-1": list(standalone)}
        out["questions"] = []
    else:
        # Collapse all inlined positions to "-1" since there are no blocks left.
        flat: list[dict[str, Any]] = []
        for qs in inlined.values():
            flat.extend(qs)
        out["inlined_questions_by_block_idx"] = {"-1": flat}
    return out


def _merge_chips_with_questions(section: dict[str, Any]) -> dict[str, Any]:
    """Within a single section, match each chip block to a same-numbered
    question. For each match: drop the chip block, record the question's
    inline position. The matched question is also removed from the
    standalone ``questions`` list so it renders ONCE (inline at the chip).

    Returns a NEW section dict with these fields possibly updated:
        - blocks: chips for matched pairs removed
        - embedded_figures: placement_block_idx remapped to surviving block indices
        - questions: matched questions filtered out
        - inlined_questions_by_block_idx: dict[str(new_after_idx), question]
          (key is "-1" for "before any block")

    Unmatched chips (chip with no same-numbered question in this section)
    are left in place — they still serve as informational pointers.
    """
    blocks: list[dict[str, Any]] = list(section.get("blocks") or [])
    questions: list[dict[str, Any]] = list(section.get("questions") or [])
    figures: list[dict[str, Any]] = list(section.get("embedded_figures") or [])

    if not blocks or not questions:
        section["inlined_questions_by_block_idx"] = {}
        return section

    # Match chip → question. Two-tier:
    #   Tier 1 (primary): normalised number equality. Catches "Exercise 4.3"
    #          chip ↔ "Exercise 4.3" question, and (via sub-question
    #          normalisation in _chip_number) "Exercise 1(iii)" → 1.3 chip ↔
    #          "Exercise 1.3" question.
    #   Tier 2 (fallback): section_id match. When numbering schemes drift
    #          (e.g. chip "Exercise 7(ii)" with no roman map AND question
    #          stored under section_ref ending in -exercise-7-ii), the chip's
    #          section_id and the question's section_ref still align.
    used_q: set[int] = set()
    matches: list[tuple[int, int]] = []  # (chip_block_idx, question_idx)
    for bi, b in enumerate(blocks):
        cn = _chip_number(b)
        chip_sid = b.get("section_id")
        matched = False
        # Tier 1: number
        if cn:
            for qi, q in enumerate(questions):
                if qi in used_q:
                    continue
                if _question_number(q) == cn:
                    matches.append((bi, qi))
                    used_q.add(qi)
                    matched = True
                    break
        # Tier 2: section_id
        if not matched and chip_sid:
            for qi, q in enumerate(questions):
                if qi in used_q:
                    continue
                q_sid = q.get("section_ref") or ""
                if q_sid == chip_sid or (
                    q_sid and chip_sid and q_sid.startswith(chip_sid + "-")
                ):
                    matches.append((bi, qi))
                    used_q.add(qi)
                    break

    if not matches:
        section["inlined_questions_by_block_idx"] = {}
        return section

    dropped_chip_idx: dict[int, int] = {bi: qi for bi, qi in matches}

    # Build new blocks list (without matched chips) and track old→new mapping
    new_blocks: list[dict[str, Any]] = []
    old_to_new: list[int | None] = []
    new_idx = -1
    # Also record where each dropped chip's question should inline. The
    # convention: inline AFTER the new block at index `anchor`, where
    # anchor is the last surviving block before the dropped chip. If the
    # chip was the very first block, anchor is -1 (= inline at start).
    inlined: dict[int, list[dict[str, Any]]] = {}
    for old_idx, b in enumerate(blocks):
        if old_idx in dropped_chip_idx:
            old_to_new.append(None)
            anchor = new_idx  # last survivor's new index
            # Copy the chip's label onto the inlined question so the
            # rendered card has a prominent "EXAMPLE 4.3" / "Exercise 8.2"
            # heading. Question extraction sometimes leaves exercise_ref
            # empty even when the chip clearly identifies it.
            inlined_q = dict(questions[dropped_chip_idx[old_idx]])
            chip_label = (b.get("label") or "").strip()
            if chip_label and not (inlined_q.get("exercise_ref") or "").strip():
                inlined_q["exercise_ref"] = chip_label
            inlined.setdefault(anchor, []).append(inlined_q)
        else:
            new_idx += 1
            new_blocks.append(b)
            old_to_new.append(new_idx)

    # Remap figure placement_block_idx
    new_figures: list[dict[str, Any]] = []
    for f in figures:
        idx = f.get("placement_block_idx")
        if idx is None:
            new_figures.append(f)
            continue
        if 0 <= idx < len(old_to_new):
            mapped = old_to_new[idx]
            if mapped is None:
                # Figure was anchored to a dropped chip — demote to trailing
                new_figures.append({**f, "placement_block_idx": None})
            else:
                new_figures.append({**f, "placement_block_idx": mapped})
        else:
            new_figures.append(f)

    # Filter out matched questions
    new_questions = [q for qi, q in enumerate(questions) if qi not in used_q]

    # Convert int keys to str (JSON serialisable, frontend reads as string)
    inlined_str = {str(k): v for k, v in inlined.items()}

    out = dict(section)
    out["blocks"] = new_blocks
    out["embedded_figures"] = new_figures
    out["questions"] = new_questions
    out["inlined_questions_by_block_idx"] = inlined_str
    return out


def _figure_dict(
    ref: FigureReference,
    fig: Figure,
) -> dict[str, Any]:
    """Render-ready figure dict, shape compatible with EmbeddedFigure on
    the frontend. Variant: regen if approved, else original."""
    variant = "regen" if (fig.regen_image_bytes and fig.approved_at) else "original"
    return {
        "ref_id": str(ref.id),
        "figure_id": str(fig.id),
        "label": fig.figure_number or ref.placeholder_text or "",
        "caption": fig.caption or "",
        "variant": variant,
        "image_url": f"/api/figures/{fig.id}/image?variant=auto",
        "placement_kind": ref.placement_kind or "appended",
        "placement_block_idx": ref.placement_block_idx,
        "placement_char_offset": ref.placement_char_offset,
        # Which part of a question this figure belongs to: "question" (stem)
        # vs "solution". Lets Preview/Composer/DOCX render it in the correct
        # body — matching the extraction/regen review view. NULL = legacy /
        # unknown → renderers default it to the question side.
        "body_target": ref.body_target,
    }


async def build_final_merge(
    session: AsyncSession,
    book_id: UUID,
    *,
    prefer_regen: bool = True,
) -> dict[str, Any]:
    """Compose the final merged document for a book.

    When ``prefer_regen`` is True (default), regenerated content overrides
    the original wherever available — per-section for theory, per-bank for
    questions, per-figure for images (variant=regen if approved). Sections
    without any regen fall back to the original Section.blocks. Mix is
    fine: some sections regen, others original.
    """
    book = await session.get(Book, book_id)
    if book is None:
        raise ValueError(f"Book {book_id} not found")

    # 1. Schema → ordered sections
    if not book.schema:
        # No schema means no order — return book metadata with empty body
        return {
            "book": {
                "id": str(book.id),
                "title": book.title or "",
                "subject": book.subject or "",
            },
            "sections": [],
            "unattached_figures": [],
        }
    try:
        schema_obj = BookSchema(**book.schema)
    except Exception as e:
        logger.warning("Schema parse failed for book %s: %s", book_id, e)
        return {
            "book": {
                "id": str(book.id),
                "title": book.title or "",
                "subject": book.subject or "",
            },
            "sections": [],
            "unattached_figures": [],
        }
    ordered_schema_sections = flatten_sections(schema_obj)

    # 2. Bulk-load Section rows (theory blocks)
    sec_rows = (
        await session.execute(
            select(Section).where(Section.book_id == book_id)
        )
    ).scalars().all()
    section_by_id = {s.section_id: s for s in sec_rows}
    # Phase 3 of canonical identity migration (CONTRACT.md §1):
    # build a title-based fallback index so when ss.id (schema's slug)
    # doesn't match Section.section_id (DB's slug) — today's Ammonia bug
    # — we can still resolve the section via title. Every fallback hit
    # is logged so we can audit slug divergence in prod.
    sections_by_title: dict[str, list] = {}
    for _s in sec_rows:
        sections_by_title.setdefault((_s.title or "").strip().lower(), []).append(_s)

    def _resolve_section_row(_ss):
        """Return Section row matching schema section _ss, with logged fallbacks.

        Lookup order:
          1. Exact slug match (Section.section_id == _ss.id) — current path
          2. Title-based fallback — Section.title == _ss.title, single match
          3. None — true miss, drop as before
        """
        row = section_by_id.get(_ss.id)
        if row is not None:
            return row
        # Title fallback
        candidates = sections_by_title.get((_ss.title or "").strip().lower(), [])
        if len(candidates) == 1:
            logger.info(
                "final_merge: slug-mismatch title-fallback fired "
                "(book=%s, schema_slug=%r, matched_slug=%r, title=%r)",
                book_id, _ss.id, candidates[0].section_id, _ss.title,
            )
            return candidates[0]
        if len(candidates) > 1:
            # Ambiguous — multiple Section rows share this title
            # (e.g. "Uses" appears under Silicon, Phosphorus, Sulphur).
            # Refuse to guess. Log so we know how often this happens.
            logger.warning(
                "final_merge: slug-mismatch title-fallback ambiguous "
                "(book=%s, schema_slug=%r, title=%r, candidates=%d)",
                book_id, _ss.id, _ss.title, len(candidates),
            )
        return None

    # 2b. Theory regen overlay — for each section_id, find the LATEST
    # regen that contains blocks for it. The regen-side blocks_by_section
    # is filtered to user-saved sections after `/regenerations/{id}/save`
    # is called, so presence here means the user opted in for this section.
    theory_regen_blocks: dict[str, list[dict]] = {}
    theory_regen_meta: dict[str, dict[str, Any]] = {}
    if prefer_regen:
        regens = (
            await session.execute(
                select(Regeneration)
                .where(Regeneration.book_id == book_id)
                .order_by(Regeneration.created_at.desc())
            )
        ).scalars().all()
        for r in regens:
            bbs = r.blocks_by_section or {}
            if not isinstance(bbs, dict):
                continue
            for sid, blocks in bbs.items():
                if sid in theory_regen_blocks:
                    continue  # already covered by newer regen
                if not isinstance(blocks, list):
                    continue
                # Note: empty list `[]` IS allowed in — it's the
                # suppression sentinel written by the recap worker for
                # PTR-redistributed and rename-promoted source sections.
                # The main loop later detects sid present with empty list
                # and skips the section entirely (no fallback to original).
                theory_regen_blocks[sid] = blocks
                theory_regen_meta[sid] = {
                    "regen_id": str(r.id),
                    "regen_created_at": r.created_at.isoformat() if r.created_at else None,
                }

    # 3. Bulk-load figure_references for this book (split by context, exclude
    #    hidden + unattached).
    all_refs = (
        await session.execute(
            select(FigureReference).where(FigureReference.book_id == book_id)
        )
    ).scalars().all()
    fig_ids = {r.figure_id for r in all_refs}

    # 3b. AUTO-HEAL — workers race when calling the figure embedder at
    # their tails (extract/figures_tasks/questions_v3 all call it). If the
    # last writer ran before bank flipped "ready" (or before questions
    # were committed), question-context figures end up unattached even
    # though the data to attach them now exists. Detect that narrow case
    # here and re-run the embedder ONCE.
    #
    # Narrow conditions (all must hold) so we never heal a healthy book:
    #   - bank.status in ("ready", "partial")              → questions are loadable
    #   - >0 figures with context_hint="question"          → there's work to do
    #   - 0 FigureReference rows attach to a question_id   → none attached
    #   - the figures' question_no values intersect the
    #     bank's question_number set                       → heal CAN succeed
    # Failure is swallowed — current refs are served unchanged.
    # Unconditional auto-heal: re-run the embedder on EVERY read so
    # figure placements are always fresh, regardless of how many figures
    # are currently attached or what their state is.
    #
    # Rationale: every Preview/Composer/Export read is a snapshot of
    # state at that moment. Source data changes constantly — questions
    # get rescued via Q-1 retry, restored via "Mark all reviewed",
    # sections re-extracted, schema edited. Running the embedder per
    # read guarantees figure_references reflects the CURRENT data, not
    # a stale snapshot from whichever worker happened to finish last.
    #
    # Cost: pure compute, ~100ms-1s per read for typical books. No
    # Gemini calls. DB: a DELETE + INSERT of ~26 rows per book.
    # Idempotent — running 100x on settled data produces identical refs.
    #
    # Replaces the previous "narrow trigger" auto-heal (which only fired
    # when ZERO question figures were attached). That narrow check was
    # too restrictive — it left partial attachments stuck (e.g. 19 of
    # 24 figures attached, 5 stuck in page_fallback because their target
    # questions came in after the embedder's snapshot). Unconditional
    # re-run catches this and all related timing/race bugs.
    try:
        from app.services.figure_embedder import embed_figures_for_book
        await embed_figures_for_book(session, book_id)
        await session.flush()
        all_refs = (
            await session.execute(
                select(FigureReference)
                .where(FigureReference.book_id == book_id)
            )
        ).scalars().all()
        fig_ids = {r.figure_id for r in all_refs}
        logger.debug(
            "auto_heal (unconditional): book=%s refs=%d",
            book_id, len(all_refs),
        )
    except Exception as e:
        logger.warning(
            "auto_heal skipped (book=%s, non-fatal): %s", book_id, e
        )
    figs = (
        await session.execute(
            select(Figure).where(Figure.id.in_(fig_ids))
        )
    ).scalars().all() if fig_ids else []
    fig_by_id = {f.id: f for f in figs}

    theory_by_section: dict[str, list[dict[str, Any]]] = {}
    question_figures_by_qid: dict[str, list[dict[str, Any]]] = {}
    unattached_figs: list[dict[str, Any]] = []

    for r in all_refs:
        f = fig_by_id.get(r.figure_id)
        if f is None:
            continue
        d = _figure_dict(r, f)
        if r.is_hidden:
            continue
        if r.placement_kind == "unattached":
            # Keep the section_ref + label on the dict so the draft seeder can
            # bind this figure to its matching `fig` placeholder block BY LABEL
            # (the same way the extraction/regen review view places it inline in
            # the right section). Anything the seeder can't label-bind stays in
            # the global "Unattached Figures" tray.
            d2 = {**d, "context": r.context, "section_ref": r.section_ref,
                  "page_number": f.page_number}
            unattached_figs.append(d2)
            continue
        if r.context == "theory":
            theory_by_section.setdefault(r.section_ref, []).append(d)
        elif r.context == "question":
            if r.question_id is not None:
                question_figures_by_qid.setdefault(str(r.question_id), []).append(d)

    for k, lst in theory_by_section.items():
        lst.sort(key=lambda d: (
            d.get("placement_block_idx") if d.get("placement_block_idx") is not None else 10**9
        ))
    for k, lst in question_figures_by_qid.items():
        lst.sort(key=lambda d: (
            d.get("placement_char_offset") if d.get("placement_char_offset") is not None else 10**9
        ))

    # 4. Bulk-load questions. Prefer the latest "saved" question
    # regeneration's variant questions over originals when prefer_regen is
    # on; questions are scoped per section_ref where the regen applied.
    # Accept both "ready" (clean finish, counts['failed']==0) and "partial"
    # (some sections failed but questions are still in the DB) so that a
    # crashed-and-recovered bank still surfaces its questions in Composer /
    # Preview. The startup recovery routine downgrades stuck-at-'extracting'
    # banks to 'partial' when questions exist; without accepting 'partial'
    # here, chip-merge + figure embedding would silently fail.
    latest_bank = (
        await session.execute(
            select(QuestionBank)
            .where(QuestionBank.book_id == book_id)
            .where(QuestionBank.status.in_(["ready", "partial"]))
            .order_by(QuestionBank.created_at.desc())
            .limit(1)
        )
    ).scalars().first()

    saved_qregen = None
    qregen_section_scope: set[str] | None = None
    if prefer_regen:
        saved_qregen = (
            await session.execute(
                select(QuestionRegeneration)
                .where(QuestionRegeneration.book_id == book_id)
                .where(QuestionRegeneration.status == "saved")
                .order_by(QuestionRegeneration.created_at.desc())
                .limit(1)
            )
        ).scalars().first()
        if saved_qregen and saved_qregen.scope == "sections":
            qregen_section_scope = set(saved_qregen.section_refs or [])

    questions_by_section: dict[str, list[Question]] = {}
    # First load originals (latest ready bank, regen_id IS NULL) — these are
    # the baseline. Regen variants replace per-section below.
    if latest_bank is not None:
        qs = (
            await session.execute(
                select(Question)
                .where(Question.bank_id == latest_bank.id)
                .where(Question.regen_id.is_(None))
                .where(Question.is_hidden.is_(False))
                .order_by(Question.section_ref, Question.page_start, Question.created_at)
            )
        ).scalars().all()
        for q in qs:
            if q.section_ref:
                questions_by_section.setdefault(q.section_ref, []).append(q)

    # Source-question lookup for variants. Regen variants are saved with
    # question_number=None (worker bug — see question_regen_v3.py history);
    # when a variant lacks a number, fall back to its source's. Without this
    # fallback, Preview/Composer/DOCX can't sort the regen-prefer items list
    # (everything has the same "missing" sort key → no reorder). Built from
    # the originals we just loaded — they're guaranteed present here even on
    # books whose variants now drive the merge.
    _source_qnum_by_id: dict[str, Any] = {}
    for _sec_qs in questions_by_section.values():
        for _orig in _sec_qs:
            _source_qnum_by_id[str(_orig.id)] = _orig.question_number

    # Now overlay saved regen questions per section (replacing originals
    # for that section).
    if saved_qregen is not None:
        regen_qs = (
            await session.execute(
                select(Question)
                .where(Question.regen_id == saved_qregen.id)
                .where(Question.is_hidden.is_(False))
                .order_by(Question.section_ref, Question.created_at)
            )
        ).scalars().all()
        # Determine which sections to overlay
        if qregen_section_scope is None:
            # Bank-scope regen — overlay every section that has regen qs
            sections_to_overlay = {q.section_ref for q in regen_qs if q.section_ref}
        else:
            sections_to_overlay = qregen_section_scope
        # Clear originals for those sections
        for sid in sections_to_overlay:
            questions_by_section[sid] = []
        for q in regen_qs:
            if q.section_ref and q.section_ref in sections_to_overlay:
                questions_by_section.setdefault(q.section_ref, []).append(q)

    # Sort each section's questions by parsed question_number so cards
    # render in natural numeric order (Q1, Q2, ..., Q10) instead of the
    # SQL row order (which was effectively by created_at and produced
    # sequences like Q2, Q3, Q1, Q4 reported by reviewers).
    import re as _re

    def _qnum_sort_key(q) -> tuple:
        raw = (getattr(q, "question_number", "") or "").strip()
        if not raw:
            return (10**9,)  # blanks sink to the end
        # Split on non-digit separators: "1.10(ii)" -> [1, 10, 2]
        # Roman numerals (i, ii, iii, iv, v) → numeric 1..5 so 1.(ii) > 1.(i)
        roman = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5,
                 "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}
        parts: list[int] = []
        for tok in _re.split(r"[^\w]+", raw):
            tok = tok.strip().lower()
            if not tok:
                continue
            if tok.isdigit():
                parts.append(int(tok))
            elif tok in roman:
                parts.append(roman[tok])
            else:
                # Mixed alpha — fall through using ord of first char so it
                # at least sorts deterministically.
                parts.append(ord(tok[0]) + 1000)
        return tuple(parts) if parts else (10**9,)

    for _sec_ref, _qs in questions_by_section.items():
        _qs.sort(key=_qnum_sort_key)

    # 5. Assemble ordered sections
    # CROSS-SECTION CHIP MATCHING — schemas often split worked examples into
    # their own subsection (e.g. "3-percentage-example-3-1"). A chip in the
    # parent section ("3-percentage") then has its target question filed
    # under the subsection, so the per-section chip-merge below would not
    # find a match and the chip stays orphaned while the subsection renders
    # a duplicate full card. To fix: augment each section's question pool
    # with questions from its descendants tagged with `_origin_section_id`.
    # If a chip consumes a descendant's question, we later skip that
    # subsection so it doesn't render twice.
    desc_by_section: dict[str, set[str]] = {}

    def _collect_descendants(node):
        out_ids: set[str] = set()
        for sub in (getattr(node, "subsections", None) or []):
            if getattr(sub, "type", "") == "excluded":
                continue
            out_ids.add(sub.id)
            out_ids |= _collect_descendants(sub)
        return out_ids

    def _walk_desc(nodes):
        for n in nodes:
            if getattr(n, "type", "") == "excluded":
                continue
            desc_by_section[n.id] = _collect_descendants(n)
            _walk_desc(getattr(n, "subsections", None) or [])

    _walk_desc(getattr(schema_obj, "sections", []) or [])

    # E1 fix — Cross-section chip-question matching.
    # Theory sections (e.g. "4.1-theory") emit exercise_ref / example_ref
    # chips pointing to numbered questions, but the actual questions live
    # in SIBLING sections like "4.1-classical-thinking" or
    # "4.1-critical-thinking" — NOT in the theory section's descendants.
    # We build a prefix-keyed sibling lookup so the theory section's
    # question pool can be extended with sibling questions, letting
    # `_merge_chips_with_questions` inline them at chip positions.
    # Same prefix rule = same numeric/dotted id stem (e.g. "4.1") shared
    # across the *-theory, *-classical-thinking, *-critical-thinking siblings.
    _PREFIX_RE = _re.compile(r"^([0-9]+(?:\.[0-9]+)*)")
    siblings_by_prefix: dict[str, list[str]] = {}
    for ss_other in ordered_schema_sections:
        m = _PREFIX_RE.match(ss_other.id or "")
        if m:
            siblings_by_prefix.setdefault(m.group(1), []).append(ss_other.id)

    def _question_to_dict(q, *, origin_section_id: str | None = None) -> dict[str, Any]:
        # Variant fallback: regen variants in the DB have question_number=None
        # (legacy worker behaviour); pull the textbook number from the source
        # original so Preview/Composer/DOCX can sort + label correctly.
        # Originals (no source_question_id) just keep their own q.question_number.
        _qnum = q.question_number or _source_qnum_by_id.get(
            str(getattr(q, "source_question_id", None) or "")
        )
        qd: dict[str, Any] = {
            "id": str(q.id),
            "question_number": _qnum,
            "exercise_ref": q.exercise_ref,
            "section_ref": q.section_ref,
            "page_start": q.page_start,
            "question_type": q.question_type,
            "raw_text": q.raw_text or "",
            # E2 fix: propagate has_options so DOCX exporter renders the
            # MCQ Options block (was being dropped because this field
            # never made it onto the final-draft question dict).
            "has_options": bool(getattr(q, "has_options", False)),
            "has_solution": bool(q.has_solution),
            "solution_text": q.solution_text or "",
            "kind": q.kind,
            # Regen variants do NOT inherit the SOURCE question's figure: a
            # regenerated question has new values, so the original figure would
            # be misleading. The regenerated LaTeX/SVG diagram (below) is the
            # only image a variant shows — and if that's absent/failed, the
            # variant shows no figure (never the stale original). Originals
            # (no source_question_id) use their own figures as before.
            "embedded_figures": question_figures_by_qid.get(str(q.id), []),
            "image_regen_hint": _extract_image_regen_hint(q),
            # Step 2 — carry the regen diagram so the Composer/Final DOCX export
            # can embed it in place of the original figure.
            "regenerated_diagram": _extract_regenerated_diagram(q),
        }
        if origin_section_id is not None:
            qd["_origin_section_id"] = origin_section_id
        return qd

    out_sections: list[dict[str, Any]] = []
    for ss in ordered_schema_sections:
        # SUPPRESSION SENTINEL — recap redistribute/promote writes [] for
        # sections whose content has been moved into other sections (PTR
        # bullets redistributed, Konnect/Note/Info-Edge section folded
        # into preceding topic). When the regen row carries an explicit
        # empty list for a section, skip the section entirely from the
        # final draft — do NOT fall back to Section.blocks original.
        if (
            prefer_regen
            and ss.id in theory_regen_blocks
            and len(theory_regen_blocks[ss.id]) == 0
        ):
            continue

        sec_row = _resolve_section_row(ss)  # Phase 3: slug + title fallback
        # Prefer regen blocks if available; fall back to original blocks
        regen_blocks = theory_regen_blocks.get(ss.id) if prefer_regen else None
        if regen_blocks:
            blocks = regen_blocks
            block_source = "regen"
        else:
            blocks = (sec_row.blocks if sec_row and sec_row.blocks else []) or []
            block_source = "original"
        section_figures = theory_by_section.get(ss.id, [])
        section_questions = questions_by_section.get(ss.id, [])

        # Build the question pool: section's own questions PLUS descendant
        # questions (tagged with their origin) so chips can match across
        # subsections. Descendant questions stay in their own section's
        # question list too — the consumed-tracking step below decides
        # whether to skip the subsection entirely.
        question_dicts: list[dict[str, Any]] = [
            _question_to_dict(q, origin_section_id=ss.id)
            for q in section_questions
        ]
        added_sids: set[str] = {ss.id}
        for desc_sid in desc_by_section.get(ss.id, set()):
            if desc_sid in added_sids:
                continue
            for q in questions_by_section.get(desc_sid, []):
                question_dicts.append(
                    _question_to_dict(q, origin_section_id=desc_sid)
                )
            added_sids.add(desc_sid)

        # E1 fix — Sibling pool expansion for theory sections with chips.
        # When a section's blocks contain exercise_ref / example_ref /
        # question_ref chips that no descendant question can satisfy,
        # pull in questions from sibling sections sharing the same
        # numeric prefix (4.1-theory ↔ 4.1-classical-thinking ↔
        # 4.1-critical-thinking). Limit to sections with chips so we
        # don't over-share questions to non-chip-bearing sections.
        has_chip_blocks = any(
            isinstance(b, dict)
            and b.get("t") in ("example_ref", "exercise_ref", "question_ref")
            for b in (blocks or [])
        )
        if has_chip_blocks:
            sid_m = _PREFIX_RE.match(ss.id or "")
            if sid_m:
                prefix = sid_m.group(1)
                for sib_sid in siblings_by_prefix.get(prefix, []):
                    if sib_sid in added_sids:
                        continue
                    for q in questions_by_section.get(sib_sid, []):
                        question_dicts.append(
                            _question_to_dict(q, origin_section_id=sib_sid)
                        )
                    added_sids.add(sib_sid)

        # Skip purely empty sections, EXCEPT container parents — those keep
        # their heading visible even with blank body so the schema hierarchy
        # is complete in Preview / Composer / DOCX. A section is a container
        # if the schema gave it non-excluded subsections; those children
        # render as their own out_sections entries and carry the actual
        # content. Dropping the parent would visually orphan the children.
        has_descendants = bool(desc_by_section.get(ss.id, set()))
        if (
            not blocks
            and not section_figures
            and not question_dicts
            and not has_descendants
        ):
            continue

        out_sections.append({
            "section_id": ss.id,
            "section_title": ss.title,
            "level": ss.level,
            "blocks": blocks,
            "block_source": block_source,
            "regen_meta": theory_regen_meta.get(ss.id),
            "embedded_figures": section_figures,
            "questions": question_dicts,
        })

    # End-of-chapter banks (e.g. "PRACTICE QUESTIONS - CLASSROOM WING").
    # These live in the schema's ``excluded_sections`` list — excluded from
    # THEORY extraction, but their questions are extracted into the question
    # bank with the section_ref set to the excluded section's title verbatim.
    # Auto-append them to the final merge so the chapter feels complete.
    # User can remove them individually from the Composer if they want.
    used_excluded_titles: set[str] = set()
    # Build a normalized index over questions_by_section so we can find
    # excluded-section questions even when the writer stored them under a
    # slightly different key (whitespace, case, trailing punctuation, or
    # an explicit id rather than the title verbatim). Avoids silently
    # losing end-of-chapter banks like "PRACTICE QUESTIONS - CLASSROOM WING".
    def _norm_key(s: str) -> str:
        return " ".join((s or "").lower().split()).strip(" .:-")
    normalized_index: dict[str, list] = {}
    for k, v in questions_by_section.items():
        normalized_index.setdefault(_norm_key(k), []).extend(v)

    def _excluded_q_dict(q) -> dict[str, Any]:
        # Same shape as the regular-section question dict so excluded-bank
        # questions carry figures + regen hints identically. Regen variants
        # fall back to the SOURCE question's figures (own-then-source) and
        # to the source's question_number (variants are stored with
        # question_number=None; see _question_to_dict above for context).
        _qnum = q.question_number or _source_qnum_by_id.get(
            str(getattr(q, "source_question_id", None) or "")
        )
        return {
            "id": str(q.id),
            "question_number": _qnum,
            "exercise_ref": q.exercise_ref,
            "section_ref": q.section_ref,
            "page_start": q.page_start,
            "question_type": q.question_type,
            "raw_text": q.raw_text or "",
            "has_options": bool(getattr(q, "has_options", False)),
            "has_solution": bool(q.has_solution),
            "solution_text": q.solution_text or "",
            "kind": q.kind,
            # Regen variants do NOT inherit the source figure (would be stale);
            # they show the regenerated diagram below, or no figure. Originals
            # use their own figures.
            "embedded_figures": question_figures_by_qid.get(str(q.id), []),
            "image_regen_hint": _extract_image_regen_hint(q),
            # Step 2 — carry the regen diagram so excluded-bank regen questions
            # embed it in place of the original figure (same as regular sections).
            "regenerated_diagram": _extract_regenerated_diagram(q),
        }

    def _min_page(qs) -> int:
        ps = [getattr(q, "page_start", None) for q in qs]
        ps = [p for p in ps if isinstance(p, int)]
        return min(ps) if ps else 10**9

    for ex in getattr(schema_obj, "excluded_sections", []) or []:
        title = (ex.title or "").strip()
        if not title:
            continue
        # Excluded end-of-chapter banks are subdivided by SUB-WING in the
        # question section_ref: "<BANK TITLE>::<sub-wing>" — e.g.
        # "CLASSROOM WING::Short Answer Type Questions" or
        # "Critical Thinking::1.3 Argand diagram". The previous lookup matched
        # ONLY the bare bank title, silently dropping every sub-wing question
        # (~half of all questions across affected books — e.g. Complex Numbers
        # surfaced 46/271). Gather EVERY question whose section_ref is the bank
        # title OR "title::*", bucketed by sub-wing, so each renders under the
        # bank as its own ordered sub-heading. Regen variants are already
        # overlaid into questions_by_section per ref above, so a saved regen on
        # a sub-wing surfaces here at the same position automatically.
        title_norm = _norm_key(title)
        buckets: dict[str, list] = {}
        for ref, qs in questions_by_section.items():
            head, sep, tail = (ref or "").partition("::")
            if _norm_key(head) == title_norm:
                key = tail.strip() if sep else ""
                buckets.setdefault(key, []).extend(qs)
        # Legacy fallback: explicit id / normalized exact (banks with no
        # "::" sub-wing structure).
        if not buckets:
            ex_id = getattr(ex, "id", None) or ""
            qs = (
                questions_by_section.get(title, [])
                or (questions_by_section.get(ex_id, []) if ex_id else [])
                or normalized_index.get(title_norm, [])
            )
            if qs:
                buckets[""] = qs
        if not buckets:
            continue  # excluded section with no extracted questions → skip
        used_excluded_titles.add(title)

        # Bare-title questions render directly on the bank heading; sub-wings
        # become ordered sub-sections beneath it (document/page order).
        direct = buckets.pop("", [])
        out_sections.append({
            "section_id": title,
            "section_title": title,
            "level": 2,
            "blocks": [],
            "block_source": "original",
            "regen_meta": None,
            "embedded_figures": [],
            "questions": [_excluded_q_dict(q) for q in direct],
        })
        for subwing in sorted(buckets, key=lambda k: _min_page(buckets[k])):
            out_sections.append({
                "section_id": f"{title}::{subwing}",
                "section_title": subwing,
                "level": 3,
                "blocks": [],
                "block_source": "original",
                "regen_meta": None,
                "embedded_figures": [],
                "questions": [_excluded_q_dict(q) for q in buckets[subwing]],
            })

    # SYNTHETIC RECAP SECTIONS: when the recap worker writes orphan-
    # fallback section(s) into blocks_by_section (e.g.
    # zzz-key-takeaways-orphan-fallback), they aren't part of the
    # original schema → the main loop above didn't emit them. Append
    # them at the end of the document so orphan PTR bullets surface.
    emitted_sids = {s["section_id"] for s in out_sections}
    for sid, blocks in theory_regen_blocks.items():
        if sid in emitted_sids:
            continue
        if not blocks:
            continue  # suppression sentinel — never emit
        # Derive a heading from the first h3 block, else from the sid.
        heading_title = sid
        for b in blocks:
            if b.get("t") == "h3" and b.get("c"):
                heading_title = b["c"]
                break
        out_sections.append({
            "section_id": sid,
            "section_title": heading_title,
            "level": 1,
            "blocks": blocks,
            "block_source": "regen",
            "regen_meta": theory_regen_meta.get(sid),
            "embedded_figures": [],
            "questions": [],
        })

    # Final pass: within each section, match chip placeholders with their
    # actual question entities and inline the question at the chip's spot
    # (dropping the chip + the duplicate standalone question). Idempotent
    # for sections that have no chips or no questions.
    merged_sections = [_merge_chips_with_questions(s) for s in out_sections]

    # Track which questions were consumed by chip-match (across sections —
    # cross-section matching is enabled above by tagging descendant
    # questions with `_origin_section_id`). When a subsection's question
    # is fully consumed via its parent's chip, we drop that subsection
    # from the output to prevent a duplicate full-card render.
    consumed_qids_by_origin: dict[str, set[str]] = {}
    for s in merged_sections:
        for qs in (s.get("inlined_questions_by_block_idx") or {}).values():
            for q in qs:
                origin = q.get("_origin_section_id")
                qid = q.get("id")
                if origin and qid:
                    consumed_qids_by_origin.setdefault(origin, set()).add(qid)

    # Drop sections whose every question got consumed elsewhere AND that
    # have no theory blocks / figures of their own to justify a render.
    # Preserves parent + sibling sections; only collapses worked-example
    # subsections whose content has been hoisted to the parent via chip.
    pruned_sections: list[dict[str, Any]] = []
    for s in merged_sections:
        sid = s.get("section_id")
        consumed_here = consumed_qids_by_origin.get(sid or "", set())
        own_questions = s.get("questions") or []
        # Strip origin tag from outgoing questions + filter out any that
        # were consumed via a parent chip.
        kept_questions: list[dict[str, Any]] = []
        for q in own_questions:
            qid = q.get("id")
            origin = q.get("_origin_section_id")
            # Only keep questions whose origin == this section (don't ship
            # the duplicates that were borrowed from descendants — those
            # are/were owned by the descendant section's own row).
            if origin and origin != sid:
                continue
            if qid in consumed_here:
                continue
            q2 = {k: v for k, v in q.items() if k != "_origin_section_id"}
            kept_questions.append(q2)
        s["questions"] = kept_questions

        # Also strip _origin_section_id from inlined questions (frontend
        # doesn't need internal tracking metadata).
        if s.get("inlined_questions_by_block_idx"):
            cleaned: dict[str, list[dict[str, Any]]] = {}
            for k, qs in s["inlined_questions_by_block_idx"].items():
                cleaned[k] = [
                    {kk: vv for kk, vv in q.items() if kk != "_origin_section_id"}
                    for q in qs
                ]
            s["inlined_questions_by_block_idx"] = cleaned

        # Drop this section if it's a worked-example subsection whose
        # question was hoisted via a parent's chip-match. Without this,
        # the example renders twice: once as the inlined question card in
        # the parent, once as a standalone section with redundant solution
        # blocks here. The subsection's blocks are typically just
        # solution_text already attached to the question — dropping them
        # is the intended dedup.
        #
        # We DO keep figures around — if the subsection had its own
        # figure that didn't make it onto the question's
        # embedded_figures, the user would lose visual content. So we
        # demote the section to a figures-only "trailer" appended to the
        # parent? No — that's overengineering. Cleanest: drop entirely.
        # The figure embedder already attaches question-context figures
        # to the question's embedded_figures, so they ride along on the
        # inlined card. Section-level figures here would be a rare edge
        # case we accept losing in exchange for not duplicating examples.
        if (
            sid
            and sid in consumed_qids_by_origin
            and not kept_questions
        ):
            # Original drop reason: a worked-example subsection whose
            # only question got hoisted to its parent via chip-match
            # would otherwise render twice — once as the inlined Q in
            # the parent's flow, once as a standalone section here with
            # its own blocks (which typically duplicate the solution
            # prose). Dropping the section eliminates the visible
            # duplicate body.
            #
            # BUT — when the subsection has NO blocks AND NO embedded
            # figures (illustration-style chip-only Cat A children:
            # title + 1 question, nothing else printed), keeping it as
            # a heading-only stub costs nothing in the markdown/JSON
            # exporters (they emit just the heading line) and lets
            # final_draft.seed_draft_items_from_merge render a uniform
            # sub-heading at the parent's chip position. Without the
            # stub, single-Q children render as bare questions while
            # multi-Q children get a clean sub-heading — visually
            # inconsistent in Preview/Composer.
            # Body = theory blocks only. Embedded figures are NOT body
            # here — a page_fallback figure attached to an
            # illustration-style chip-only Cat A child (no blocks of
            # its own) belongs to the child's render flow; without it
            # the figure is lost entirely (the question itself doesn't
            # always carry the figure when placement_kind=page_fallback).
            # We keep the figures on the stub so they render under the
            # child's sub-heading; the original "drop duplicate body"
            # rationale only applies when the child actually has
            # theory blocks that would duplicate the parent's flow.
            has_body = bool(s.get("blocks"))
            if has_body:
                continue
            # Heading-only stub: preserve section_id / title / level /
            # embedded_figures metadata; empty blocks so no duplicate
            # render of any theory body. Restore THIS section's own
            # questions (those whose origin == sid) onto the stub even
            # though they were consumed via parent chip — the downstream
            # renderer (final_draft._emit_inlined_at) REPLACES the
            # parent's inlined Q with a recursive child render, so the
            # Q's content must live inside the child or it's silently
            # lost. The parent's anchor still carries the Q (used only
            # as the trigger to inline this child); the Q itself only
            # actually emits once — inside the child section.
            s["blocks"] = []
            restored_qs: list[dict[str, Any]] = []
            for q in own_questions:
                q_origin = q.get("_origin_section_id")
                if q_origin and q_origin != sid:
                    continue  # don't restore borrowed-from-descendant Qs
                restored_qs.append(
                    {k: v for k, v in q.items() if k != "_origin_section_id"}
                )
            s["questions"] = restored_qs

        # F8 extension — also drop a worked-example subsection that
        # consumed its OWN chip within itself. The chip-merge inlined the
        # question; the section's remaining theory blocks repeat the
        # solution prose. Without this, preview shows the inlined Q4.2
        # immediately followed by the standalone EXAMPLE 4.2 with its
        # broken-up solution paragraphs — visually identical content.
        #
        # Tight match conditions so we don't accidentally collapse a real
        # subsection that happens to have a chip-match:
        #   1. section id contains "-example-" OR title starts "EXAMPLE "
        #   2. chip-merge actually inlined a question here
        #   3. no standalone questions remain
        title = (s.get("section_title") or "").strip().upper()
        inlined_map = s.get("inlined_questions_by_block_idx") or {}
        had_inline = any(qs for qs in inlined_map.values())
        # Match any question-kind section (Example, Exercise, Problem,
        # Practice, etc) — both by id pattern and by title prefix. Whatever
        # form the schema uses, if the section is question-content and the
        # chip-merge picked up its content, the standalone section
        # rendering is redundant.
        sid_lower = (sid or "").lower()
        looks_like_question_section = (
            "-example-" in sid_lower
            or "-exercise-" in sid_lower
            or "-problem-" in sid_lower
            or "-practice-" in sid_lower
            or "-question-" in sid_lower
            or title.startswith("EXAMPLE ")
            or title.startswith("EXERCISE ")
            or title.startswith("WORKED EXAMPLE")
            or title.startswith("PROBLEM ")
            or title.startswith("PRACTICE ")
        )
        if looks_like_question_section and not kept_questions:
            # If chip-match inlined its questions (had_inline) OR this
            # section was hoisted via a parent chip (consumed_qids_by_origin
            # already caught above), drop it. The "had_inline OR consumed"
            # check now covers both same-section and cross-section cases.
            had_inline = any(qs for qs in (s.get("inlined_questions_by_block_idx") or {}).values())
            was_consumed = sid in consumed_qids_by_origin
            if had_inline or was_consumed:
                continue

        pruned_sections.append(s)
    merged_sections = pruned_sections

    # Second pass: for worked-example sections where the merge ran and
    # the inlined question carries solution_text, drop the now-redundant
    # OCR'd solution blocks. Deterministic, structural signal only.
    merged_sections = [_drop_solution_duplicate_blocks(s) for s in merged_sections]
    # Third pass: for REGULAR sections that contain inline EXAMPLE block
    # ranges, drop those ranges when a matching same-numbered question
    # with solution_text exists. Handles the "EXAMPLE 8.8 inside Pith
    # Ball Electroscope section" case where the example is a sub-region
    # of a larger section. Deterministic — no AI.
    merged_sections = [_drop_in_section_worked_examples(s) for s in merged_sections]

    # ZERO-LOSS SAFETY NET — guarantee every extracted question is visible.
    # Despite the section / chip-inline / excluded-bank machinery above, a
    # small tail can still fall through: a Cat A subsection pruned on the
    # assumption its question was inlined into a parent (when the chip text
    # didn't actually match → the inline never landed), or a section_ref that
    # maps to no schema node at all (self-test-N, bare "1", orphaned sub-wing
    # slugs). Rather than chase every fragile chip/prune edge case (which would
    # risk the ~14k questions that DO place correctly), we enforce the
    # invariant additively: any non-hidden question not yet emitted anywhere is
    # re-attached at the end — titled by its schema node when known — so it is
    # visible in Composer / Preview / export instead of silently dropped.
    emitted_qids: set[str] = set()
    for s in merged_sections:
        for q in (s.get("questions") or []):
            emitted_qids.add(str(q.get("id")))
        for qs in (s.get("inlined_questions_by_block_idx") or {}).values():
            for q in qs:
                emitted_qids.add(str(q.get("id")))
    node_title_by_id = {ss.id: (ss.title or ss.id) for ss in ordered_schema_sections}
    node_level_by_id = {ss.id: ss.level for ss in ordered_schema_sections}
    # Schema-order index — used to slot a recovered section back into its
    # natural document position rather than dumping it at the very end.
    order_index = {ss.id: i for i, ss in enumerate(ordered_schema_sections)}

    missing_by_ref: dict[str, list] = {}
    seen_missing: set[str] = set()
    for ref, qs in questions_by_section.items():
        for q in qs:
            qid = str(q.id)
            if qid in emitted_qids or qid in seen_missing:
                continue
            seen_missing.add(qid)
            missing_by_ref.setdefault(ref or "(uncategorized)", []).append(q)

    # Resolve a question's section_ref to its schema node id. The question
    # extractor and the schema sometimes disagree on prefixing — the node may
    # be parent-prefixed ("1.6-self-test-5") while the question ref is short
    # ("self-test-5"), or vice-versa. Match exactly, else by UNAMBIGUOUS
    # suffix in either direction (exactly one candidate). Ambiguous / no match
    # → None (those go to the uncategorized tail).
    def _resolve_node(ref: str) -> str | None:
        if ref in order_index:
            return ref
        cands = [
            nid for nid in order_index
            if nid.endswith("-" + ref) or ref.endswith("-" + nid)
        ]
        return cands[0] if len(cands) == 1 else None

    # Index existing emitted sections by id so we can ATTACH recovered
    # questions onto the section the main loop already emitted (typically an
    # empty heading, because the ref mismatch left it question-less) — this
    # places them at the node's exact schema position with no duplicate.
    section_by_id: dict[str, dict[str, Any]] = {}
    for s in merged_sections:
        section_by_id.setdefault(s.get("section_id"), s)

    def _new_section(node_id: str, qs: list) -> dict[str, Any]:
        return {
            "section_id": node_id,
            "section_title": node_title_by_id.get(node_id, node_id),
            "level": node_level_by_id.get(node_id) or 3,
            "blocks": [],
            "block_source": "original",
            "regen_meta": None,
            "embedded_figures": [],
            "questions": [_excluded_q_dict(q) for q in qs],
        }

    uncategorized: list = []
    # Process in schema order so multiple new inserts land correctly.
    for ref, qs in sorted(
        missing_by_ref.items(),
        key=lambda kv: order_index.get(_resolve_node(kv[0]) or "", 1 << 30),
    ):
        node_id = _resolve_node(ref)
        if node_id is None:
            uncategorized.extend(qs)
            continue
        existing = section_by_id.get(node_id)
        if existing is not None:
            # Attach onto the already-emitted section (dedup by qid).
            have = {str(q.get("id")) for q in (existing.get("questions") or [])}
            existing.setdefault("questions", []).extend(
                _excluded_q_dict(q) for q in qs if str(q.id) not in have
            )
            continue
        # Node not emitted by the main loop → insert at its schema-order spot,
        # before the first later-ordered section / non-schema section.
        oidx = order_index[node_id]
        sec = _new_section(node_id, qs)
        insert_at = len(merged_sections)
        for i, ms in enumerate(merged_sections):
            ms_oidx = order_index.get(ms.get("section_id"))
            if ms_oidx is None or ms_oidx > oidx:
                insert_at = i
                break
        merged_sections.insert(insert_at, sec)
        section_by_id[node_id] = sec

    # Truly uncategorized (ref maps to no schema node) → visible at the end.
    if uncategorized:
        merged_sections.append(_new_section("(uncategorized)", uncategorized))
        merged_sections[-1]["section_title"] = "Additional Questions"

    return {
        "book": {
            "id": str(book.id),
            "title": book.title or "",
            "subject": book.subject or "",
        },
        "sections": merged_sections,
        "unattached_figures": unattached_figs,
    }


__all__ = ["build_final_merge"]
