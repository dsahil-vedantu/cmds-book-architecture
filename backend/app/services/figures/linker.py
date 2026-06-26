"""Figure linker — CPU-only post-extraction pass.

Takes Gemini's figure metadata (page, context, question_ref, caption) and
maps each figure to our schema's `section_ref` + (when applicable) a
specific `question.id`. Zero Gemini calls — pure indexing.

Why we need this: Gemini's `context` is "theory"|"question"|"solution" and
`question_ref` is free-text ("Q3", "Example 5"). Our world uses
canonical `section_ref` strings (e.g. "5-progression-hp") and UUID
question ids. The linker bridges the two.

Strategy:
  1. Flatten the book schema into (section_id, title, page_start, page_end)
  2. For each figure, locate the schema section whose page-range covers
     `figure.page`. That's the primary `section_ref` anchor.
  3. If the figure's context is "question"/"solution":
       - Try to match question_ref text against question.exercise_ref +
         question.question_number on rows in the bank that fall in the
         same section
       - Pick the closest match (page-aware), set `question_id`
  4. If the figure is bound to BOTH theory and a question (dual-context
     rule from the upstream prompt), the linker emits TWO
     `figure_references` rows — one with context='theory', one with
     context='question'. The underlying Figure row is shared.

Returns a list of FigureReferenceCandidate dicts the caller persists.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# Loose label normalization — used as one half of the composite link key
# (book + section + normalized_label). Same regex everywhere — prompt
# placeholder lookups must use the same normalization.
_LABEL_PREFIX_RE = re.compile(r"^\s*(figure|fig\.?|figure no\.?|fig)\s*", re.IGNORECASE)


def normalize_label(raw: str | None) -> str:
    """`"Figure 8.1"` / `"Fig 8.1"` / `"FIGURE 8-1"` -> `"8.1"`."""
    if not raw:
        return ""
    s = raw.strip()
    s = _LABEL_PREFIX_RE.sub("", s)
    # Strip surrounding punctuation
    s = s.strip(" .:-")
    # Normalize dashes/spaces between numeric parts
    s = re.sub(r"\s+", "", s)
    return s.lower()


def normalize_question_ref(raw: str | None) -> str:
    """Normalize Gemini's `question_ref` for matching ("Q3", "Q.3",
    "Question 3", "Example 5") -> a comparable key.
    """
    if not raw:
        return ""
    s = raw.strip().lower()
    s = re.sub(r"^q(?:uestion)?\.?\s*", "q", s)
    s = re.sub(r"^example\s*", "example", s)
    s = re.sub(r"^problem\s*", "problem", s)
    s = re.sub(r"\s+", "", s)
    return s


# ---------------------------------------------------------------------------
# Schema flattening
# ---------------------------------------------------------------------------

def flatten_schema_sections(schema: dict | None) -> list[dict[str, Any]]:
    """Walk book.schema_ pre-order to produce a flat list of
    (section_id, title, page_start, page_end, type) tuples.

    Skip excluded sections; include their parent.
    """
    out: list[dict[str, Any]] = []
    if not schema:
        return out
    seen: set[str] = set()

    def walk(node: dict) -> None:
        sid = node.get("id")
        if sid and sid not in seen and (node.get("type") or "section") != "excluded":
            out.append({
                "section_id": sid,
                "title": node.get("title") or sid,
                "page_start": node.get("page_start"),
                "page_end": node.get("page_end"),
                "type": node.get("type") or "section",
            })
            seen.add(sid)
        for child in node.get("subsections") or []:
            walk(child)

    for top in schema.get("sections") or []:
        walk(top)
    return out


def section_for_page(
    flat_sections: list[dict[str, Any]],
    page: int | None,
) -> dict[str, Any] | None:
    """Pick the most-specific section whose [page_start, page_end] covers
    `page`. Most-specific = smallest page-range containing it.
    """
    if page is None:
        return None
    candidates: list[dict[str, Any]] = []
    for s in flat_sections:
        ps, pe = s.get("page_start"), s.get("page_end")
        if ps is None or pe is None:
            continue
        if ps <= page <= pe:
            candidates.append(s)
    if not candidates:
        return None
    # Smaller range first (most specific = leaf section)
    candidates.sort(key=lambda s: (s["page_end"] - s["page_start"], s["section_id"]))
    return candidates[0]


# ---------------------------------------------------------------------------
# Question linking
# ---------------------------------------------------------------------------

def link_to_question(
    gemini_question_ref: str | None,
    section_ref: str,
    questions_in_section: list[dict[str, Any]],
) -> str | None:
    """Try to identify which question in `questions_in_section` matches
    Gemini's free-text `question_ref` (e.g. "Q3", "Example 5").

    Returns the question's UUID as string, or None when no good match.
    """
    if not gemini_question_ref or not questions_in_section:
        return None

    qref_norm = normalize_question_ref(gemini_question_ref)
    if not qref_norm:
        return None

    # Build per-question comparable keys
    def key_for(q: dict[str, Any]) -> set[str]:
        keys: set[str] = set()
        for raw in (
            q.get("question_number"),
            q.get("exercise_ref"),
            q.get("section_title"),
        ):
            n = normalize_question_ref(raw)
            if n:
                keys.add(n)
            # Also accept the digit-only form
            digits = re.sub(r"[^0-9.]+", "", str(raw or ""))
            if digits:
                keys.add("q" + digits)
        return keys

    for q in questions_in_section:
        if qref_norm in key_for(q):
            return str(q.get("id"))

    # Fallback: substring match (e.g. "Q3" inside "Q3.a")
    for q in questions_in_section:
        for k in key_for(q):
            if qref_norm in k or k in qref_norm:
                return str(q.get("id"))
    return None


# ---------------------------------------------------------------------------
# Main entry — build link candidates for the worker to persist
# ---------------------------------------------------------------------------

def build_link_candidates(
    gemini_figures: list[dict[str, Any]],
    schema: dict | None,
    questions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """For each Gemini figure entry, produce one or more link candidates.

    A candidate is a dict shaped like:
        {
          "figure_id_text": "fig_8_1",     # Gemini's id (composite key half)
          "normalized_label": "8.1",
          "section_ref": "5-progression-ap",
          "context": "theory" | "question",
          "question_id": "<uuid>" | None,
          "placeholder_text": "Figure 8.1",
          "page": 5,
          "caption": "Figure 8.1 …",
          "bounding_box": [ymin, xmin, ymax, xmax],
          "type": "diagram",
          "raw_context": "theory",    # Gemini's verbatim context value
          "raw_question_ref": "Q3" | None,
        }

    Dual-context (same figure for both theory + a question) emits TWO
    candidates — same Gemini id, same bbox, different (context, question_id).

    The caller decides whether to persist as ONE Figure + TWO references
    (recommended for Q2 normalized model) or two Figure rows.
    """
    flat = flatten_schema_sections(schema)
    # Index questions by section for O(1) lookup per figure
    by_section: dict[str, list[dict[str, Any]]] = {}
    for q in questions:
        ref = q.get("section_ref")
        if ref:
            by_section.setdefault(ref, []).append(q)

    out: list[dict[str, Any]] = []
    for fig in gemini_figures:
        page = fig.get("page")
        section = section_for_page(flat, page)
        section_ref = section["section_id"] if section else None
        if not section_ref:
            logger.warning(
                "figure %s on page %s — no schema section covers this page; "
                "leaving section_ref empty (orphan)",
                fig.get("id"), page,
            )

        raw_ctx = (fig.get("context") or "").strip().lower()
        norm_label = normalize_label(fig.get("figure_label"))
        # Positional-linking metadata for unlabelled figures. is_labelled
        # defaults to True so historical extractions (which lack the
        # field) keep treating themselves as labelled. anchor_text /
        # anchor_position / question_no are non-null only when the new
        # prompt's UNLABELLED FIGURE EXTRACTION section fired.
        is_labelled = fig.get("is_labelled")
        if is_labelled is None:
            # Inferred fallback for older runs / safety net
            is_labelled = bool(fig.get("figure_label"))
        base = {
            "figure_id_text": fig.get("id"),
            "normalized_label": norm_label,
            "section_ref": section_ref or "",
            "placeholder_text": fig.get("figure_label"),
            "page": page,
            "caption": fig.get("caption"),
            # Preserve Gemini's 2-3 sentence figure description so the
            # downstream writer can persist it on the Figure row. Without
            # this, figures_tasks.py:_extract_figures_v2 reads
            # head.get("description") → None → description column NULL on
            # every figure (observed today: 0/25 description populated on
            # the test book despite the writer fix at line 406). The
            # description is required for UNLABELLED figure placeholder
            # rendering and for figure regen prompts.
            "description": fig.get("description"),
            "bounding_box": fig.get("bounding_box"),
            "type": fig.get("type"),
            "raw_context": raw_ctx,
            "raw_question_ref": fig.get("question_ref"),
            # Positional fields — only meaningful when is_labelled=False
            "is_labelled": bool(is_labelled),
            "anchor_text": fig.get("anchor_text"),
            "anchor_position": fig.get("anchor_position"),
            "question_no": fig.get("question_no"),
        }

        # Map Gemini's context vocabulary to ours
        if raw_ctx in ("question", "solution"):
            # Labelled path: match by question_ref. Unlabelled path: fall
            # back to question_no when question_ref is missing.
            qid = link_to_question(
                fig.get("question_ref") or fig.get("question_no"),
                section_ref or "",
                by_section.get(section_ref or "", []),
            )
            cand = {**base, "context": "question", "question_id": qid}
            out.append(cand)
        elif raw_ctx == "theory":
            cand = {**base, "context": "theory", "question_id": None}
            out.append(cand)
        else:
            # "other" or unknown — emit as theory by default (UI can filter)
            cand = {**base, "context": "theory", "question_id": None}
            out.append(cand)
    return out


def merge_dual_context(candidates: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group link candidates by Gemini's `figure_id_text`.

    Gemini emits TWO entries with the same id when a figure is bound to
    both theory and a question (dual-context rule). After
    `build_link_candidates`, these become two candidates with the same
    figure_id_text and different context/question_id.

    Returns `{figure_id_text: [candidate, ...]}`. The worker creates ONE
    Figure row per key and N figure_references per value-list.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for c in candidates:
        key = c.get("figure_id_text") or ""
        if not key:
            continue
        grouped.setdefault(key, []).append(c)
    return grouped
