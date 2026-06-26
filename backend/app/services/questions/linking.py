"""Deterministic question → section linking cascade.

Pure functions, no LLM, no I/O. Each rule returns a :class:`LinkResult` with
a confidence constant. Callers run ``resolve_block_link`` for block-level
matching and ``refine_question_link`` to let per-question signals override.

The cascade is intentionally verbose and readable — every rule is testable
with synthetic fixtures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

# Public rule names (stored in `questions.link_method`)
RULE_TITLE_PATTERN = "title_pattern"
RULE_PAGE_CONTAINED = "page_contained"
RULE_PAGE_ADJACENT = "page_adjacent"
RULE_QUESTION_NUMBER = "question_number"
RULE_INLINE_REFERENCE = "inline_reference"
RULE_CHAPTER_TITLE = "chapter_title"
RULE_NEAREST_CHAPTER = "nearest_chapter"
RULE_USER_OVERRIDE = "user_override"

# Per-rule confidence constants
CONFIDENCE = {
    RULE_TITLE_PATTERN: 0.95,
    RULE_PAGE_CONTAINED: 0.90,
    RULE_PAGE_ADJACENT: 0.85,
    RULE_QUESTION_NUMBER: 0.80,
    RULE_INLINE_REFERENCE: 0.75,
    RULE_CHAPTER_TITLE: 0.70,
    RULE_NEAREST_CHAPTER: 0.40,
    RULE_USER_OVERRIDE: 1.00,
}

# Regex library — compiled once
_RE_DOTTED_ID = re.compile(r"\b(\d+(?:\.\d+)+)\b")
_RE_SINGLE_INT = re.compile(r"^\s*(\d+)\s*$")
_RE_QUESTION_NUMBER_PREFIX = re.compile(r"^\s*(?:Q|Problem|Exercise)?\s*(\d+(?:\.\d+)*)")
_RE_INLINE_REFS = [
    re.compile(r"\(§\s*(\d+(?:\.\d+)+)\)"),
    re.compile(r"\bSection\s+(\d+(?:\.\d+)+)", re.IGNORECASE),
    re.compile(r"\bArticle\s+(\d+(?:\.\d+)+)", re.IGNORECASE),
    re.compile(r"\bRef(?:er)?\.?\s*:?\s*(\d+(?:\.\d+)+)", re.IGNORECASE),
    re.compile(r"\[from\s+(\d+(?:\.\d+)+)\]", re.IGNORECASE),
    re.compile(r"\(refer\s+to\s+(\d+(?:\.\d+)+)\)", re.IGNORECASE),
]


@dataclass
class LinkResult:
    """Outcome of a linking attempt. ``section_ref`` is ``None`` for unlinkable."""

    section_ref: str | None
    method: str
    confidence: float
    trace: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class FlatSection:
    """Flat projection of a schema section used by linking rules."""

    id: str
    title: str
    level: int
    page_start: int | None
    page_end: int | None


@dataclass
class SchemaIndex:
    """Pre-computed indices over the theory schema — cheap to build, reusable."""

    sections: list[FlatSection]
    id_set: set[str]
    chapter_ids: set[str]
    parent_of: dict[str, str | None]

    @classmethod
    def from_schema(cls, schema: dict | None) -> "SchemaIndex":
        sections: list[FlatSection] = []
        parent_of: dict[str, str | None] = {}

        def walk(arr: Iterable[dict], parent_id: str | None) -> None:
            for s in arr or []:
                if not isinstance(s, dict):
                    continue
                if s.get("type") == "excluded":
                    continue
                sid = str(s.get("id", "")).strip()
                if not sid:
                    continue
                sections.append(
                    FlatSection(
                        id=sid,
                        title=str(s.get("title", "")),
                        level=int(s.get("level", 0)),
                        page_start=_maybe_int(s.get("page_start")),
                        page_end=_maybe_int(s.get("page_end")),
                    )
                )
                parent_of[sid] = parent_id
                walk(s.get("subsections") or [], sid)

        raw = (schema or {}).get("sections") or []
        walk(raw, None)

        id_set = {s.id for s in sections}
        chapter_ids = {s.id for s in sections if s.level <= 1}

        return cls(
            sections=sections,
            id_set=id_set,
            chapter_ids=chapter_ids,
            parent_of=parent_of,
        )


def _maybe_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def is_descendant(ancestor_id: str, candidate_id: str, index: SchemaIndex) -> bool:
    """Return True iff ``candidate_id`` is a descendant of (or equal to) ``ancestor_id``."""
    if candidate_id == ancestor_id:
        return True
    cur = index.parent_of.get(candidate_id)
    while cur is not None:
        if cur == ancestor_id:
            return True
        cur = index.parent_of.get(cur)
    return False


# ---------------------------------------------------------------------------
# Rule 1 — title pattern
# ---------------------------------------------------------------------------
def rule_title_pattern(title: str, index: SchemaIndex) -> LinkResult | None:
    """Dotted ID inside an excluded block title (``Exercise 1.1`` → ``1.1``)."""
    if not title:
        return None
    for m in _RE_DOTTED_ID.finditer(title):
        candidate = m.group(1)
        if candidate in index.id_set:
            return LinkResult(
                section_ref=candidate,
                method=RULE_TITLE_PATTERN,
                confidence=CONFIDENCE[RULE_TITLE_PATTERN],
                trace=[{"rule": RULE_TITLE_PATTERN, "matched": candidate}],
            )
    return None


# ---------------------------------------------------------------------------
# Rule 2 — page containment
# ---------------------------------------------------------------------------
def rule_page_contained(
    page_start: int | None,
    page_end: int | None,
    index: SchemaIndex,
) -> LinkResult | None:
    """Excluded's pages fall inside a theory section's range — pick the deepest."""
    if page_start is None or page_end is None:
        return None
    best: FlatSection | None = None
    for s in index.sections:
        if s.page_start is None or s.page_end is None:
            continue
        if s.page_start <= page_start and s.page_end >= page_end:
            if best is None or s.level > best.level:
                best = s
    if best is not None:
        return LinkResult(
            section_ref=best.id,
            method=RULE_PAGE_CONTAINED,
            confidence=CONFIDENCE[RULE_PAGE_CONTAINED],
            trace=[{"rule": RULE_PAGE_CONTAINED, "matched": best.id, "level": best.level}],
        )
    return None


# ---------------------------------------------------------------------------
# Rule 3 — page adjacency
# ---------------------------------------------------------------------------
def rule_page_adjacent(
    page_start: int | None,
    index: SchemaIndex,
) -> LinkResult | None:
    """Excluded starts within ±1 page of a theory section's end — link to that section."""
    if page_start is None:
        return None
    best: FlatSection | None = None
    for s in index.sections:
        if s.page_end is None:
            continue
        if abs(s.page_end - page_start) <= 1:
            if best is None or (s.page_end or 0) > (best.page_end or 0):
                best = s
    if best is not None:
        return LinkResult(
            section_ref=best.id,
            method=RULE_PAGE_ADJACENT,
            confidence=CONFIDENCE[RULE_PAGE_ADJACENT],
            trace=[{"rule": RULE_PAGE_ADJACENT, "matched": best.id}],
        )
    return None


# ---------------------------------------------------------------------------
# Rule 4 — question number prefix
# ---------------------------------------------------------------------------
def rule_question_number(
    question_number: str | None,
    index: SchemaIndex,
    *,
    current_chapter_id: str | None = None,
) -> LinkResult | None:
    """Question number like ``1.1.3`` → try progressively broader prefixes.

    If no direct match, and a ``current_chapter_id`` is supplied, try prepending
    it to catch chapter-relative numbering (e.g. question "1.2" inside chapter 8
    actually means §8.1.2).
    """
    if not question_number:
        return None
    m = _RE_QUESTION_NUMBER_PREFIX.match(str(question_number))
    if not m:
        return None
    parts = m.group(1).split(".")
    while parts:
        candidate = ".".join(parts)
        if candidate in index.id_set:
            return LinkResult(
                section_ref=candidate,
                method=RULE_QUESTION_NUMBER,
                confidence=CONFIDENCE[RULE_QUESTION_NUMBER],
                trace=[{"rule": RULE_QUESTION_NUMBER, "matched": candidate}],
            )
        parts.pop()
    # Chapter-relative fallback
    if current_chapter_id:
        parts = m.group(1).split(".")
        while parts:
            prefixed = f"{current_chapter_id}.{'.'.join(parts)}"
            if prefixed in index.id_set:
                return LinkResult(
                    section_ref=prefixed,
                    method=RULE_QUESTION_NUMBER,
                    confidence=CONFIDENCE[RULE_QUESTION_NUMBER] - 0.05,
                    trace=[
                        {
                            "rule": RULE_QUESTION_NUMBER,
                            "matched": prefixed,
                            "chapter_relative": True,
                        }
                    ],
                )
            parts.pop()
    return None


# ---------------------------------------------------------------------------
# Rule 5 — inline reference in stem
# ---------------------------------------------------------------------------
def rule_inline_reference(text: str | None, index: SchemaIndex) -> LinkResult | None:
    """Question stem contains ``(§1.2)``, ``Section 1.2``, etc."""
    if not text:
        return None
    snippet = text[:400]
    for pat in _RE_INLINE_REFS:
        m = pat.search(snippet)
        if m:
            candidate = m.group(1)
            if candidate in index.id_set:
                return LinkResult(
                    section_ref=candidate,
                    method=RULE_INLINE_REFERENCE,
                    confidence=CONFIDENCE[RULE_INLINE_REFERENCE],
                    trace=[{"rule": RULE_INLINE_REFERENCE, "matched": candidate}],
                )
    return None


# ---------------------------------------------------------------------------
# Rule 6 — chapter title (single integer in excluded title matches a chapter ID)
# ---------------------------------------------------------------------------
def rule_chapter_title(title: str, index: SchemaIndex) -> LinkResult | None:
    if not title:
        return None
    m = _RE_SINGLE_INT.search(title)
    if m and m.group(1) in index.chapter_ids:
        return LinkResult(
            section_ref=m.group(1),
            method=RULE_CHAPTER_TITLE,
            confidence=CONFIDENCE[RULE_CHAPTER_TITLE],
            trace=[{"rule": RULE_CHAPTER_TITLE, "matched": m.group(1)}],
        )
    # Also handle "Exercises 8", "Problems 8" with a dotted ID scan as fallback
    for word in title.split():
        if word in index.chapter_ids:
            return LinkResult(
                section_ref=word,
                method=RULE_CHAPTER_TITLE,
                confidence=CONFIDENCE[RULE_CHAPTER_TITLE] - 0.05,
                trace=[{"rule": RULE_CHAPTER_TITLE, "matched": word, "fuzzy": True}],
            )
    return None


# ---------------------------------------------------------------------------
# Rule 7 — nearest preceding chapter by page
# ---------------------------------------------------------------------------
def rule_nearest_chapter(
    page_start: int | None,
    index: SchemaIndex,
) -> LinkResult | None:
    if page_start is None or not index.chapter_ids:
        return None
    best: FlatSection | None = None
    for s in index.sections:
        if s.id not in index.chapter_ids:
            continue
        if s.page_start is None:
            continue
        if s.page_start <= page_start:
            if best is None or (s.page_start or 0) > (best.page_start or 0):
                best = s
    if best is not None:
        return LinkResult(
            section_ref=best.id,
            method=RULE_NEAREST_CHAPTER,
            confidence=CONFIDENCE[RULE_NEAREST_CHAPTER],
            trace=[{"rule": RULE_NEAREST_CHAPTER, "matched": best.id}],
        )
    return None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def resolve_block_link(
    *,
    title: str,
    page_start: int | None,
    page_end: int | None,
    index: SchemaIndex,
) -> LinkResult:
    """Run block-level rules (1, 2, 3, 6, 7). First match wins; always returns a result."""
    trace: list[dict[str, Any]] = []

    for rule in (
        lambda: rule_title_pattern(title, index),
        lambda: rule_page_contained(page_start, page_end, index),
        lambda: rule_page_adjacent(page_start, index),
        lambda: rule_chapter_title(title, index),
        lambda: rule_nearest_chapter(page_start, index),
    ):
        r = rule()
        if r is None:
            trace.append({"rule": "skip", "info": "no match"})
            continue
        r.trace = trace + r.trace
        return r

    # Ultimate fallback: unlinked
    return LinkResult(
        section_ref=None,
        method=RULE_NEAREST_CHAPTER,
        confidence=0.0,
        trace=trace + [{"rule": "fallback", "info": "no link"}],
    )


def refine_question_link(
    *,
    block_link: LinkResult,
    question_number: str | None,
    stem_text: str | None,
    index: SchemaIndex,
) -> LinkResult:
    """Apply per-question rules (4, 5). Override block-level only if strictly more specific."""
    block_ref = block_link.section_ref
    current_chapter: str | None = None
    if block_ref:
        cur = block_ref
        while cur is not None and cur not in index.chapter_ids:
            cur = index.parent_of.get(cur)
        current_chapter = cur

    for r in (
        rule_question_number(question_number, index, current_chapter_id=current_chapter),
        rule_inline_reference(stem_text, index),
    ):
        if r is None or r.section_ref is None:
            continue
        # Only override if refined link is a descendant of (or equal to) the block-level link.
        if block_ref is None or is_descendant(block_ref, r.section_ref, index):
            combined_trace = list(block_link.trace) + list(r.trace)
            return LinkResult(
                section_ref=r.section_ref,
                method=r.method,
                confidence=max(block_link.confidence, r.confidence),
                trace=combined_trace,
            )
    return block_link


def linking_stats(results: list[LinkResult]) -> dict[str, Any]:
    """Summarize a list of LinkResults for persistence on QuestionBank.linking_stats."""
    by_method: dict[str, int] = {}
    unlinked = 0
    for r in results:
        by_method[r.method] = by_method.get(r.method, 0) + 1
        if r.section_ref is None:
            unlinked += 1
    return {
        "total": len(results),
        "unlinked": unlinked,
        "by_method": by_method,
    }
