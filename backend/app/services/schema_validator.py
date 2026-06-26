"""Schema validator — hard rules, fail loud, no silent fixes.

Replaces `_sanitize_schema`'s silent-drop behavior with structured
validation errors that:
  1. Surface every problem to the caller
  2. Carry enough context to drive a corrective retry (see
     `schema_correctors.py`)

The validator is PURE — no DB I/O, no Gemini call. Caller decides
what to do based on the returned `ValidationResult`.

Rules are added incrementally — each Day in SCHEMA Week 1-2 adds
more. Today (Day 3):
  * Rule 1 (NON_INTEGER_PAGE):   page values must be positive integers
  * Rule 2 (INVERTED_RANGE):     page_start ≤ page_end
  * Rule 3 (PAGE_OUT_OF_BOUNDS): 1 ≤ pages ≤ pdf_total_pages

Day 4-5 add rules 4-10. Together they're the SOLE gate before a
schema is accepted as `done`.

Usage:
    result = validate_schema(parsed_data, pdf_total_pages=42)
    if not result.is_valid:
        # Drive corrective retry — see schema_correctors.build_corrective_prompt
        ...
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator


class ErrorType(str, Enum):
    """Error types correspond 1:1 with corrective prompt fragments in
    schema_correctors.py. Adding a new error type requires adding a
    matching corrective."""

    # Day 3 (implemented):
    NON_INTEGER_PAGE = "non_integer_page"
    INVERTED_RANGE = "inverted_range"
    PAGE_OUT_OF_BOUNDS = "page_out_of_bounds"
    # Day 4 (implemented — your Q4 case):
    INDIVIDUAL_QUESTION_AS_SECTION = "individual_question_as_section"
    # Day 6 (implemented — structural integrity):
    PAGE_OUTSIDE_PARENT = "page_outside_parent"
    SIBLING_PAGE_OVERLAP = "sibling_page_overlap"
    PAGE_COVERAGE_GAP = "page_coverage_gap"
    INVALID_TYPE = "invalid_type"
    MISSING_LEAF_PAGE = "missing_leaf_page"
    # Day 7 (implemented):
    INVALID_CONTENT_TYPES = "invalid_content_types"
    CAT_A_AT_END_NOT_EXCLUDED = "cat_a_at_end_not_excluded"
    EMPTY_PLACEHOLDER = "empty_placeholder"
    # Theory Unit 1 — excluded_sections hard rules (§0.5 #4):
    TITLE_DUPLICATE_ACROSS_ARRAYS = "title_duplicate_across_arrays"
    MID_CHAPTER_EXCLUDED = "mid_chapter_excluded"
    # Theory Unit 9 followup — §5.9 puzzle block prohibition:
    PUZZLE_AS_SECTION = "puzzle_as_section"
    # Theory followup — Cat A must nest under preceding Cat B section.
    CAT_A_NOT_NESTED_UNDER_PREVIOUS_THEORY = "cat_a_not_nested_under_previous_theory"
    # Positional Cat A — parent's page range must contain Cat A's page_start.
    CAT_A_PARENT_PAGE_MISMATCH = "cat_a_parent_page_mismatch"
    # Whole-schema page coverage — every page of the PDF must appear in
    # sections[] or excluded_sections[].
    SCHEMA_PAGE_COVERAGE_INCOMPLETE = "schema_page_coverage_incomplete"


# Canonical set — used by Rule 7 and others.
_VALID_SECTION_TYPES = frozenset({
    "chapter", "section", "subsection", "excluded",
})

# Rule 10 — canonical content_types vocabulary.
# Sections describe what they CONTAIN: theory prose, questions, figures
# (or some combination). Order doesn't matter; we treat as sets.
_VALID_CONTENT_TYPE_VALUES = frozenset({
    "theory", "questions", "figures",
})

# Allowed COMBINATIONS (as frozensets — order-independent).
# Mixed ["theory", "questions"] is FORBIDDEN per v2 architecture
# (prompts/v2/schema_architecture.txt §2.2): a section's content_types
# describes its OWN direct content, NOT what nested children contain.
# Parent stays ["theory"]; Cat A children carry ["questions"] separately.
# Downstream extractor walks children and reads each child's own tag.
_VALID_CONTENT_TYPE_COMBOS = frozenset({
    frozenset({"theory"}),
    frozenset({"questions"}),
    frozenset({"theory", "figures"}),
    frozenset({"questions", "figures"}),
})

# Rule 11 — standalone help-section titles that ALWAYS belong in
# excluded_sections regardless of position. These are typically
# chapter-end aids that don't fit the inline numbered pattern.
# Matched as exact phrase (case-insensitive) — substring matching
# would false-positive on titles like "Worked Solutions to Example 8.1".
_END_OF_CHAPTER_HELP_TITLES = frozenset({
    "hints", "solutions", "answers",
    "answer key", "answer keys",
    "key to exercises", "key to problems",
})


@dataclass(frozen=True)
class ValidationError:
    """A single validation failure. Structured for corrective retry."""

    type: ErrorType
    """Error category — drives the corrective prompt selection."""

    section_id: str | None
    """The offending section's id, if applicable. None for whole-schema errors."""

    section_title: str | None
    """Human-readable section title for the corrective prompt."""

    severity: str
    """'error' (must fix) | 'warning' (accept if quality_score above threshold)."""

    message: str
    """Plain-English explanation for logs + UI surfacing."""

    context: dict[str, Any] = field(default_factory=dict)
    """Extra data the corrective prompt builder needs (e.g. the bad value,
    expected range, parent reference)."""


@dataclass(frozen=True)
class ValidationResult:
    """Outcome of running all validators against a schema."""

    is_valid: bool
    """True if zero ERROR-severity issues. Warnings allowed."""

    errors: list[ValidationError]
    """ERROR-severity issues. Block schema acceptance, drive corrective retry."""

    warnings: list[ValidationError]
    """WARNING-severity issues. Surface to user via book.schema_warnings."""

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)


# ─── RULE IMPLEMENTATIONS ──────────────────────────────────────────


def _iter_all_sections(data: dict) -> Iterator[tuple[dict, list[str]]]:
    """Walk every section (including excluded_sections) yielding the
    section dict and its parent-chain (list of titles for context).
    """
    def walk(sections, parent_chain):
        for s in sections or []:
            if not isinstance(s, dict):
                continue
            yield s, parent_chain
            yield from walk(
                s.get("subsections") or [],
                parent_chain + [s.get("title", "")],
            )

    yield from walk(data.get("sections") or [], [])
    # excluded_sections too — they have page_start/page_end and matter
    yield from walk(data.get("excluded_sections") or [], ["<excluded>"])


def _iter_with_parent(
    data: dict,
) -> Iterator[tuple[dict, dict | None, list[dict], bool]]:
    """Walk every section yielding (section, parent, siblings, is_excluded).

    `parent` is None for top-level sections (no parent in schema).
    `siblings` is the list this section is part of (so we can pair-check
    sibling overlaps). For top-level sections, siblings == data["sections"].
    `is_excluded` is True iff the section is in `excluded_sections` (or any
    nested subsections inside excluded). Used by the main loop to skip
    rules that don't apply to ExcludedSection's schema (no type/content_types
    fields).

    excluded_sections also yielded (with parent=None, siblings=data["excluded_sections"]).
    """
    sections = data.get("sections") or []

    def walk(siblings, parent, is_excluded):
        for s in siblings or []:
            if not isinstance(s, dict):
                continue
            yield s, parent, siblings, is_excluded
            yield from walk(s.get("subsections") or [], s, is_excluded)

    yield from walk(sections, None, False)
    yield from walk(data.get("excluded_sections") or [], None, True)


def _check_non_integer_page(
    section: dict, _parents: list[str]
) -> list[ValidationError]:
    """Rule 1: page_start and page_end must be positive integers OR None.

    Catches the bug we keep hitting: Gemini emits "9.18" (a question
    label) or 8.31 (a float) or "9-18" (a range string) as page values.

    `None` is allowed here — caller may treat None as "unknown, fall back
    to extraction-time correction" via E1.
    """
    out = []
    for field_name in ("page_start", "page_end"):
        v = section.get(field_name)
        if v is None:
            continue
        # Booleans are a subclass of int — guard explicitly.
        if isinstance(v, bool) or not isinstance(v, int):
            out.append(ValidationError(
                type=ErrorType.NON_INTEGER_PAGE,
                section_id=section.get("id"),
                section_title=section.get("title"),
                severity="error",
                message=(
                    f"{field_name}={v!r} is not an integer. "
                    f"Pages must be positive integers (the physical page "
                    f"where the section's content is printed)."
                ),
                context={
                    "field": field_name,
                    "value": v,
                    "value_type": type(v).__name__,
                },
            ))
        elif v < 1:
            out.append(ValidationError(
                type=ErrorType.NON_INTEGER_PAGE,
                section_id=section.get("id"),
                section_title=section.get("title"),
                severity="error",
                message=(
                    f"{field_name}={v} is not positive. Pages start at 1."
                ),
                context={"field": field_name, "value": v},
            ))
    return out


def _check_inverted_range(
    section: dict, _parents: list[str]
) -> list[ValidationError]:
    """Rule 2: page_start ≤ page_end.

    Catches Gemini emitting reversed ranges like page_start=10, page_end=5.
    Only fires when both values are integers (rule 1 catches the rest).
    """
    ps = section.get("page_start")
    pe = section.get("page_end")
    if not (isinstance(ps, int) and not isinstance(ps, bool)):
        return []
    if not (isinstance(pe, int) and not isinstance(pe, bool)):
        return []
    if ps > pe:
        return [ValidationError(
            type=ErrorType.INVERTED_RANGE,
            section_id=section.get("id"),
            section_title=section.get("title"),
            severity="error",
            message=(
                f"page_start={ps} > page_end={pe}. "
                f"A section's page_start must be ≤ page_end (sections are "
                f"contiguous, not reverse-ordered)."
            ),
            context={"page_start": ps, "page_end": pe},
        )]
    return []


def _is_individual_question_title(title: str | None) -> bool:
    """Detect titles that look like individual question identifiers.

    Patterns matched (case-insensitive):
      "4"           — bare number
      "4."          — number with period
      "Q4" / "Q.4"  — Q-prefixed
      "Question 4"  — long form
      "(4)" / "(iv)" — parenthesized
      "MCQ 4"       — MCQ-prefixed
      "Problem 4"   — problem-prefixed

    These are wrongly-promoted individual questions. They should live
    inside the parent Cat A bank's expected_question_count, not as
    standalone schema entries.

    Distinguished from LEGITIMATE Cat A section titles like
    "EXAMPLE 9.1" (worked example with explanation), "Exercise 8.3"
    (named exercise block), or "Practice Questions" (bank heading) —
    those have descriptive words and ARE valid Cat A sections.
    """
    if not title:
        return False
    import re
    t = title.strip().lower()
    patterns = [
        r"^q?\.?\s*\d+\.?$",         # "4", "4.", "q4", "q.4", "q 4"
        r"^question\s+\d+\.?$",       # "Question 4"
        r"^\(\s*\d+\s*\)$",           # "(4)"
        r"^\(\s*[ivxlcdm]+\s*\)$",   # "(iv)", "(ix)"  Roman lower
        r"^mcq\s+\d+\.?$",            # "MCQ 4"
        r"^problem\s+\d+\.?$",        # "Problem 4" (without theory context)
        r"^prob\.?\s*\d+\.?$",        # "Prob 4", "Prob.4"
    ]
    return any(re.match(p, t) for p in patterns)


def _check_individual_question_as_section(
    section: dict, _parents: list[str]
) -> list[ValidationError]:
    """Rule 9: Individual numbered questions wrongly promoted as schema sections.

    Catches the user's "Q4 on page 6 wrongly emitted as section" case.

    Triggers when:
      - section is Cat A (content_types includes "questions")
      - title matches individual-question pattern (see
        _is_individual_question_title)

    These should live inside the parent bank's
    `expected_question_count`, NOT as separate schema entries.

    Page-number-as-question-number side effect (e.g. Gemini set
    page_start=4 because it grabbed the "4" from "Question 4") is
    automatically prevented when this rule fires — corrective retry
    removes the section entirely.
    """
    title = section.get("title")
    if not _is_individual_question_title(title):
        return []

    content_types = section.get("content_types") or []
    if not isinstance(content_types, list):
        content_types = [content_types]
    is_cat_a = "questions" in content_types
    if not is_cat_a:
        # If it's tagged as theory with a number-only title, it's
        # probably TOC bullet noise — caught by a different rule. Skip.
        return []

    return [ValidationError(
        type=ErrorType.INDIVIDUAL_QUESTION_AS_SECTION,
        section_id=section.get("id"),
        section_title=title,
        severity="error",
        message=(
            f'Section "{title}" appears to be an individual numbered '
            f"question wrongly promoted to a standalone schema entry. "
            f"Individual questions belong inside their parent bank's "
            f"expected_question_count, not as separate schema sections."
        ),
        context={
            "title_pattern": "individual_question",
            "page_start": section.get("page_start"),
        },
    )]


def _check_page_outside_parent(
    child: dict, parent: dict | None
) -> list[ValidationError]:
    """Rule 4: child's page range must be ⊂ parent's range.

    Catches cross-section bleed: child claims pages outside what its
    parent says it covers. Either child is wrong or parent's range is
    too narrow — corrective asks Gemini to reconcile.

    Skipped if:
      - No parent (top-level chapter)
      - Either parent or child has None for the relevant page
    """
    if parent is None:
        return []

    out = []
    c_start = child.get("page_start")
    c_end = child.get("page_end")
    p_start = parent.get("page_start")
    p_end = parent.get("page_end")

    # Only validate when both sides have integer pages — rule 1 catches None/strings.
    def _is_int(v):
        return isinstance(v, int) and not isinstance(v, bool)

    if _is_int(c_start) and _is_int(p_start) and c_start < p_start:
        out.append(ValidationError(
            type=ErrorType.PAGE_OUTSIDE_PARENT,
            section_id=child.get("id"),
            section_title=child.get("title"),
            severity="error",
            message=(
                f'Child page_start={c_start} is before parent '
                f'"{parent.get("title")}" page_start={p_start}. '
                f"Children must start at or after their parent."
            ),
            context={
                "child_start": c_start,
                "parent_start": p_start,
                "parent_title": parent.get("title"),
            },
        ))
    if _is_int(c_end) and _is_int(p_end) and c_end > p_end:
        out.append(ValidationError(
            type=ErrorType.PAGE_OUTSIDE_PARENT,
            section_id=child.get("id"),
            section_title=child.get("title"),
            severity="error",
            message=(
                f'Child page_end={c_end} exceeds parent '
                f'"{parent.get("title")}" page_end={p_end}. '
                f"Children must end at or before their parent."
            ),
            context={
                "child_end": c_end,
                "parent_end": p_end,
                "parent_title": parent.get("title"),
            },
        ))
    return out


def _check_sibling_page_overlap(
    siblings: list[dict],
) -> list[ValidationError]:
    """Rule 5: among siblings, no two should overlap by 2+ pages.

    Boundary share (1 page common — section A ends where B starts)
    is allowed. Deep overlap (2+ shared pages) signals two sections
    fighting over the same content.

    Edge cases handled:
      - Either page None → skip the pair
      - Single sibling → no overlap possible
      - Self-comparison → skipped (i != j)
    """
    out = []

    def _is_int(v):
        return isinstance(v, int) and not isinstance(v, bool)

    # Pair-wise check; we report each conflict once (i < j)
    for i, a in enumerate(siblings):
        if not isinstance(a, dict):
            continue
        a_start = a.get("page_start")
        a_end = a.get("page_end")
        if not (_is_int(a_start) and _is_int(a_end)):
            continue
        for j in range(i + 1, len(siblings)):
            b = siblings[j]
            if not isinstance(b, dict):
                continue
            b_start = b.get("page_start")
            b_end = b.get("page_end")
            if not (_is_int(b_start) and _is_int(b_end)):
                continue
            # Compute overlap range
            overlap_start = max(a_start, b_start)
            overlap_end = min(a_end, b_end)
            if overlap_end < overlap_start:
                continue  # no overlap
            overlap_pages = overlap_end - overlap_start + 1
            if overlap_pages >= 2:
                out.append(ValidationError(
                    type=ErrorType.SIBLING_PAGE_OVERLAP,
                    section_id=a.get("id"),
                    section_title=a.get("title"),
                    severity="error",
                    message=(
                        f'Section "{a.get("title")}" (pages {a_start}-{a_end}) '
                        f'and "{b.get("title")}" (pages {b_start}-{b_end}) '
                        f"both claim pages {overlap_start}-{overlap_end} "
                        f"({overlap_pages} pages of overlap). Sections "
                        f"shouldn't cover the same content."
                    ),
                    context={
                        "section_a_title": a.get("title"),
                        "section_a_pages": [a_start, a_end],
                        "section_b_title": b.get("title"),
                        "section_b_pages": [b_start, b_end],
                        "overlap": [overlap_start, overlap_end],
                    },
                ))
    return out


def _check_page_coverage_gap(
    data: dict, pdf_total_pages: int | None
) -> list[ValidationError]:
    """Rule 6: every PDF page should be in ≥1 leaf section (including
    excluded_sections). Severity = WARNING, not error.

    Gaps are informational — some PDFs legitimately have unannotated
    pages (covers, blanks, copyright). User reviews warnings via
    schema_warnings field; not blocking.

    Edge cases:
      - pdf_total_pages None → skip (no way to compute gaps)
      - Container sections contribute pages via their children's coverage
      - Excluded sections (terminal banks) DO count toward coverage
      - Gaps grouped into ranges (page 5-7 instead of 5, 6, 7)
    """
    if pdf_total_pages is None or pdf_total_pages <= 0:
        return []

    def _is_int(v):
        return isinstance(v, int) and not isinstance(v, bool)

    covered: set[int] = set()

    def _collect(sections):
        for s in sections or []:
            if not isinstance(s, dict):
                continue
            # A section is a "leaf" for coverage purposes if it has no
            # subsections. Container pages come from descendants.
            kids = s.get("subsections") or []
            ps = s.get("page_start")
            pe = s.get("page_end")
            if not kids and _is_int(ps) and _is_int(pe) and ps <= pe:
                for p in range(ps, pe + 1):
                    if 1 <= p <= pdf_total_pages:
                        covered.add(p)
            _collect(kids)

    _collect(data.get("sections") or [])
    # Excluded sections also count
    for ex in data.get("excluded_sections") or []:
        if not isinstance(ex, dict):
            continue
        ps = ex.get("page_start")
        pe = ex.get("page_end")
        if _is_int(ps) and _is_int(pe) and ps <= pe:
            for p in range(ps, pe + 1):
                if 1 <= p <= pdf_total_pages:
                    covered.add(p)

    all_pages = set(range(1, pdf_total_pages + 1))
    missing = sorted(all_pages - covered)
    if not missing:
        return []

    # Group consecutive missing pages into ranges for cleaner messages
    ranges = []
    if missing:
        run_start = missing[0]
        run_end = missing[0]
        for p in missing[1:]:
            if p == run_end + 1:
                run_end = p
            else:
                ranges.append((run_start, run_end))
                run_start = run_end = p
        ranges.append((run_start, run_end))

    range_strs = [f"{a}" if a == b else f"{a}-{b}" for a, b in ranges]
    return [ValidationError(
        type=ErrorType.PAGE_COVERAGE_GAP,
        section_id=None,
        section_title=None,
        severity="warning",
        message=(
            f"Pages not covered by any leaf section: "
            f"{', '.join(range_strs)}. "
            f"If these pages have content (theory or questions), add a "
            f"section to cover them. Cover pages and blanks may be "
            f"legitimately uncovered."
        ),
        context={
            "missing_ranges": [list(r) for r in ranges],
            "total_missing": len(missing),
            "pdf_total_pages": pdf_total_pages,
        },
    )]


def _check_invalid_type(
    section: dict, _parents: list[str]
) -> list[ValidationError]:
    """Rule 7: section.type must be in canonical enum.

    Catches Gemini emitting non-standard types like 'unit',
    'subsubsection', 'sub-section', 'topic'. Today these are silently
    remapped by _sanitize_schema._TYPE_MAP; once that's deleted (Day 14)
    this rule becomes the sole enforcement.
    """
    t = section.get("type")
    if t is None:
        return [ValidationError(
            type=ErrorType.INVALID_TYPE,
            section_id=section.get("id"),
            section_title=section.get("title"),
            severity="error",
            message=(
                f'Section "{section.get("title")}" has no type. '
                f"Required: one of {sorted(_VALID_SECTION_TYPES)}."
            ),
            context={
                "value": None,
                "valid_types": sorted(_VALID_SECTION_TYPES),
                "level": section.get("level"),
            },
        )]
    if not isinstance(t, str):
        return [ValidationError(
            type=ErrorType.INVALID_TYPE,
            section_id=section.get("id"),
            section_title=section.get("title"),
            severity="error",
            message=(
                f'Section "{section.get("title")}" has type={t!r} '
                f"({type(t).__name__}). Type must be a string from: "
                f"{sorted(_VALID_SECTION_TYPES)}."
            ),
            context={"value": t, "valid_types": sorted(_VALID_SECTION_TYPES)},
        )]
    if t not in _VALID_SECTION_TYPES:
        return [ValidationError(
            type=ErrorType.INVALID_TYPE,
            section_id=section.get("id"),
            section_title=section.get("title"),
            severity="error",
            message=(
                f'Section "{section.get("title")}" has type="{t}". '
                f"Valid types: {sorted(_VALID_SECTION_TYPES)}."
            ),
            context={"value": t, "valid_types": sorted(_VALID_SECTION_TYPES)},
        )]
    return []


def _check_invalid_content_types(
    section: dict, _parents: list[str]
) -> list[ValidationError]:
    """Rule 10: content_types must be a valid combination from a strict vocabulary.

    Allowed values per element: {"theory", "questions", "figures"}.
    Allowed combinations:
        ["theory"]
        ["questions"]
        ["theory", "questions"]    ← Mixed (today silently collapsed by sanitizer)
        ["theory", "figures"]
        ["questions", "figures"]
        ["theory", "questions", "figures"]

    Normalization rules (per user spec):
      - Duplicates allowed in raw input; we dedupe before comparison
      - Order doesn't matter (set-based)
      - Case STRICT: only lowercase accepted (so "Theory" → error)
      - Empty list → error (must specify at least one)
      - None → error (must be specified)
    """
    ct = section.get("content_types")
    if ct is None:
        return [ValidationError(
            type=ErrorType.INVALID_CONTENT_TYPES,
            section_id=section.get("id"),
            section_title=section.get("title"),
            severity="error",
            message=(
                f'Section "{section.get("title")}" has no content_types. '
                f"Required: one of theory/questions/mixed plus optional figures."
            ),
            context={"value": None},
        )]
    if not isinstance(ct, list):
        return [ValidationError(
            type=ErrorType.INVALID_CONTENT_TYPES,
            section_id=section.get("id"),
            section_title=section.get("title"),
            severity="error",
            message=(
                f'Section "{section.get("title")}" has content_types '
                f"of type {type(ct).__name__}, expected list."
            ),
            context={"value": ct, "value_type": type(ct).__name__},
        )]
    if not ct:
        return [ValidationError(
            type=ErrorType.INVALID_CONTENT_TYPES,
            section_id=section.get("id"),
            section_title=section.get("title"),
            severity="error",
            message=(
                f'Section "{section.get("title")}" has empty content_types '
                f"list. Must specify at least one of theory/questions."
            ),
            context={"value": []},
        )]

    # Check all elements are strings + lowercase + in valid vocabulary
    for v in ct:
        if not isinstance(v, str):
            return [ValidationError(
                type=ErrorType.INVALID_CONTENT_TYPES,
                section_id=section.get("id"),
                section_title=section.get("title"),
                severity="error",
                message=(
                    f'Section "{section.get("title")}" content_types '
                    f"contains non-string: {v!r}. Expected: lowercase strings."
                ),
                context={"value": ct, "bad_element": v},
            )]
        if v != v.lower():
            return [ValidationError(
                type=ErrorType.INVALID_CONTENT_TYPES,
                section_id=section.get("id"),
                section_title=section.get("title"),
                severity="error",
                message=(
                    f'Section "{section.get("title")}" content_types '
                    f'contains "{v}" (mixed case). Must be lowercase '
                    f'(e.g. "theory" not "Theory").'
                ),
                context={"value": ct, "bad_element": v},
            )]
        if v not in _VALID_CONTENT_TYPE_VALUES:
            return [ValidationError(
                type=ErrorType.INVALID_CONTENT_TYPES,
                section_id=section.get("id"),
                section_title=section.get("title"),
                severity="error",
                message=(
                    f'Section "{section.get("title")}" content_types '
                    f'contains "{v}". Valid values: '
                    f"{sorted(_VALID_CONTENT_TYPE_VALUES)}."
                ),
                context={"value": ct, "bad_element": v},
            )]

    # Dedupe and compare as set (order/duplicates don't matter)
    normalized = frozenset(ct)
    if normalized not in _VALID_CONTENT_TYPE_COMBOS:
        return [ValidationError(
            type=ErrorType.INVALID_CONTENT_TYPES,
            section_id=section.get("id"),
            section_title=section.get("title"),
            severity="error",
            message=(
                f'Section "{section.get("title")}" content_types {ct} '
                f"is not a valid combination. Allowed: each set must "
                f"contain at least one of theory/questions, optionally figures."
            ),
            context={"value": ct, "normalized": sorted(normalized)},
        )]
    return []


def _has_inline_decimal_pattern(title: str | None) -> bool:
    """True if title contains a X.Y decimal pattern (Example 1.1, Exercise 8.3).

    Used to whitelist inline Cat A items from Rule 11. Per user spec:
    Cat A items with decimal numbering are INLINE; without decimal
    they may be chapter-end banks.

    Matches digit.digit anywhere in the title (also handles X.Y.Z, e.g. 1.2.3).
    """
    if not title:
        return False
    import re
    return bool(re.search(r"\d+\.\d+", title))


def _is_standalone_help_title(title: str | None) -> bool:
    """True if title is a standalone end-of-chapter help section.

    Hints/Solutions/Answer Keys/Answers when used as a section heading
    (not part of a longer descriptive title like "Worked Solutions to
    Example 8.1") signal a chapter-end aid that belongs in
    excluded_sections.

    Match logic: title's normalized form is exactly equal to one of
    the help phrases (or differs only by trailing punctuation/colon).
    """
    if not title:
        return False
    import re
    # Strip surrounding whitespace, lowercase, drop trailing punct/colons
    t = re.sub(r"[\s:.,;!\-]+$", "", title.strip().lower())
    return t in _END_OF_CHAPTER_HELP_TITLES


def _check_cat_a_at_end_not_excluded(
    section: dict,
    parent: dict | None,
    siblings: list[dict],
) -> list[ValidationError]:
    """Rule 11: Cat A sections at end of chapter belong in excluded_sections.

    Per user spec:
      - Cat A items with X.Y decimal pattern (Example 1.1, Exercise 8.3)
        are INLINE — nested under Cat B parent regardless of position
      - Cat A bank-style sections (Practice Questions, MCQs, Hints, etc.)
        at the END of a chapter belong in excluded_sections (flat array)
      - "End of chapter" = no Cat B (theory) sibling AFTER this section
        in the parent's children list
      - Standalone "Hints", "Solutions", "Answer Keys", "Answers" titles
        ALWAYS belong in excluded_sections (titles signal chapter-end aid)

    Fires when ALL true:
      1. Section is Cat A (content_types includes "questions")
      2. Title does NOT have X.Y decimal pattern (inline whitelist)
      3. EITHER:
         a. No Cat B section appears AFTER it in parent's subsections
         b. OR title is a standalone help-section name (always excluded)

    Returns ERROR with suggested move to excluded_sections.

    Skip cases:
      - Section is in excluded_sections (no parent in main tree, won't fire)
      - Section is a top-level chapter (parent is None — not "in a chapter")
      - X.Y decimal title → whitelist, never flag
    """
    # Must be Cat A
    ct = section.get("content_types") or []
    if not isinstance(ct, list) or "questions" not in ct:
        return []

    # Title-based whitelist: inline decimal pattern → always inline, never flag
    title = section.get("title") or ""
    if _has_inline_decimal_pattern(title):
        return []

    # Skip Cat A nested inside another Cat A — only flag the TOP-MOST
    # bank. If parent is also Cat A (e.g. "Very Short Answer Type" inside
    # "CLASSROOM WING" inside "Practice Questions"), the parent will be
    # flagged and its children move with it structurally. Flagging every
    # descendant would be noisy and redundant.
    if parent is not None:
        parent_ct = parent.get("content_types") or []
        if isinstance(parent_ct, list) and "questions" in parent_ct:
            return []

    # Standalone help title → ALWAYS flag regardless of position
    # (still only fires for top-most, since nested Cat A skipped above)
    if _is_standalone_help_title(title):
        return [ValidationError(
            type=ErrorType.CAT_A_AT_END_NOT_EXCLUDED,
            section_id=section.get("id"),
            section_title=title,
            # SCHEMA Rebalance — downgraded to warning. Position-trumps-label
            # is subjective per user spec; valid mid-chapter "Solutions" /
            # "Hints" callouts exist in some textbooks. Surface, don't block.
            severity="warning",
            message=(
                f'Section "{title}" is a standalone help section '
                f"(hints/solutions/answer keys) — these belong in "
                f"excluded_sections (flat top-level array), not nested "
                f"in the main sections tree."
            ),
            context={
                "title": title,
                "reason": "standalone_help",
            },
        )]

    # Positional check: skip top-level chapters (no parent in main tree)
    if parent is None:
        return []

    # Position check: no Cat B sibling AFTER this section?
    if section not in siblings:
        # Defensive — shouldn't happen, walker yields (section, parent, siblings)
        return []
    try:
        idx = siblings.index(section)
    except ValueError:
        return []

    # Look at siblings AFTER this section in the parent's children list
    has_cat_b_after = False
    for sib in siblings[idx + 1:]:
        sib_ct = sib.get("content_types") or []
        if isinstance(sib_ct, list) and "theory" in sib_ct:
            has_cat_b_after = True
            break

    if has_cat_b_after:
        # Cat B follows → this is NOT end-of-chapter → inline OK
        return []

    # We're a Cat A section with no Cat B after us in parent's children → end-of-chapter bank
    return [ValidationError(
        type=ErrorType.CAT_A_AT_END_NOT_EXCLUDED,
        section_id=section.get("id"),
        section_title=title,
        # SCHEMA Rebalance — downgraded to warning (see standalone-help branch).
        severity="warning",
        message=(
            f'Section "{title}" is a Cat A bank at end of its chapter '
            f"(no theory section follows it). End-of-chapter banks "
            f"belong in excluded_sections (flat top-level array), "
            f"not nested as a subsection of a theory parent."
        ),
        context={
            "title": title,
            "reason": "end_of_chapter_position",
            "parent_title": parent.get("title"),
        },
    )]


def _check_empty_placeholder(
    data: dict, pdf_total_pages: int | None
) -> list[ValidationError]:
    """Rule 12: catastrophic schema (empty sections + excluded) on a non-blank PDF.

    Fires when:
      - data["sections"] is empty/missing
      - AND data["excluded_sections"] is empty/missing
      - AND pdf_total_pages > 1 (cover-only PDFs allowed)

    This catches the worst-case Gemini failure where it returns
    {"sections": [], "excluded_sections": []} on a PDF that clearly
    has content. The prompt already has anti-placeholder guards, but
    Gemini sometimes ignores them.
    """
    sections = data.get("sections") or []
    excluded = data.get("excluded_sections") or []
    if sections or excluded:
        return []
    # Empty schema — only flag if PDF has more than trivial content
    if pdf_total_pages is None or pdf_total_pages <= 1:
        return []
    return [ValidationError(
        type=ErrorType.EMPTY_PLACEHOLDER,
        section_id=None,
        section_title=None,
        severity="error",
        message=(
            f"Schema is empty (zero sections, zero excluded_sections) "
            f"but the PDF has {pdf_total_pages} pages. Cannot proceed "
            f"with an empty schema. Re-extract with anti-placeholder "
            f"guard active."
        ),
        context={
            "sections_count": 0,
            "excluded_count": 0,
            "pdf_total_pages": pdf_total_pages,
        },
    )]


def _check_missing_leaf_page(
    section: dict, _parents: list[str]
) -> list[ValidationError]:
    """Rule 8: leaf sections MUST have page_start.

    Critical for extraction contract: a leaf section's content is
    extracted from EXACTLY its own page range. If page_start is None,
    extraction has nothing to slice — silent extraction failure.

    Container sections (with subsections) MAY have None page_start
    (derivable from min(children's page_start)). True placeholders
    (no children AND no pages) are flagged.

    page_end can be None on leaves (extractor derives from next
    section's start or chapter end — but page_start is mandatory).
    """
    subs = section.get("subsections") or []
    is_leaf = len(subs) == 0
    if not is_leaf:
        return []

    page_start = section.get("page_start")
    if isinstance(page_start, int) and not isinstance(page_start, bool):
        return []  # has a valid integer page_start

    return [ValidationError(
        type=ErrorType.MISSING_LEAF_PAGE,
        section_id=section.get("id"),
        section_title=section.get("title"),
        severity="error",
        message=(
            f'Leaf section "{section.get("title")}" has no valid '
            f"page_start. Leaf sections (no subsections) require a "
            f"physical page number — extraction CANNOT fall back to "
            f"parent's range. Either add page_start or make it a "
            f"container with subsections."
        ),
        context={
            "is_leaf": True,
            "page_start_value": page_start,
        },
    )]


def _check_page_out_of_bounds(
    section: dict, _parents: list[str], *, pdf_total_pages: int | None
) -> list[ValidationError]:
    """Rule 3: 1 ≤ page_start, page_end ≤ pdf_total_pages.

    Catches Gemini hallucinating page numbers beyond the PDF
    (e.g. page=999 on a 50-page book). Only checks if total_pages
    is provided; otherwise skipped (best-effort).
    """
    if pdf_total_pages is None or pdf_total_pages <= 0:
        return []
    out = []
    for field_name in ("page_start", "page_end"):
        v = section.get(field_name)
        if not (isinstance(v, int) and not isinstance(v, bool)):
            continue
        if v > pdf_total_pages:
            out.append(ValidationError(
                type=ErrorType.PAGE_OUT_OF_BOUNDS,
                section_id=section.get("id"),
                section_title=section.get("title"),
                severity="error",
                message=(
                    f"{field_name}={v} exceeds PDF length "
                    f"({pdf_total_pages} pages)."
                ),
                context={
                    "field": field_name,
                    "value": v,
                    "pdf_total_pages": pdf_total_pages,
                },
            ))
    return out


def _collect_titles(items: list, recurse_key: str = "subsections") -> list[tuple[str, str | None, int | None]]:
    """Flatten all titles in a tree. Returns [(title_normalized, id, page_start), ...]."""
    out: list[tuple[str, str | None, int | None]] = []

    def walk(nodes):
        for n in nodes or []:
            if not isinstance(n, dict):
                continue
            title = (n.get("title") or "").strip()
            if title:
                out.append((title, n.get("id"), n.get("page_start")))
            walk(n.get(recurse_key) or [])

    walk(items)
    return out


def _check_title_duplicate_across_arrays(data: dict) -> list[ValidationError]:
    """Theory Unit 1 (§0.5 #4c): a title appearing in `sections[]` (recursive)
    MUST NOT also appear in `excluded_sections[]` (recursive). Each piece
    of content lives in exactly one tree.

    Title comparison is exact (after strip). If excluded banks need to
    organize questions by inline section structure, they MUST use
    distinguishable names per the prompt rule.
    """
    sections = data.get("sections") or []
    excluded = data.get("excluded_sections") or []

    inline_titles = _collect_titles(sections)
    excluded_titles = _collect_titles(excluded)

    inline_title_set = {t for t, _, _ in inline_titles}

    out: list[ValidationError] = []
    seen_titles: set[str] = set()
    for title, eid, page in excluded_titles:
        if title in inline_title_set and title not in seen_titles:
            seen_titles.add(title)
            # Find the inline match to surface its location too
            inline_match = next(
                ((t, i, p) for t, i, p in inline_titles if t == title),
                (None, None, None),
            )
            out.append(ValidationError(
                type=ErrorType.TITLE_DUPLICATE_ACROSS_ARRAYS,
                section_id=eid,
                section_title=title,
                severity="error",
                message=(
                    f'Title "{title}" appears in BOTH `sections[]` '
                    f'(inline, id={inline_match[1]!r} page={inline_match[2]}) '
                    f'AND `excluded_sections[]` (excluded, page={page}). '
                    f'Each piece of content must live in exactly one array. '
                    f'Either move it inline (if mid-chapter / theory follows) '
                    f'or use a distinguishable name for the excluded entry '
                    f'(e.g. "Practice Set — {title}").'
                ),
                context={
                    "title": title,
                    "inline_page": inline_match[2],
                    "excluded_page": page,
                },
            ))
    return out


def _check_mid_chapter_excluded(data: dict) -> list[ValidationError]:
    """Theory Unit 1 (§0.5 #4b): every `excluded_sections` entry MUST appear
    AFTER the last theory section in the PDF. Mid-chapter Q-like content
    (illustrations, in-text questions, named banks between theory blocks)
    must be INLINE Cat A under its preceding theory parent, never demoted
    to excluded as a fallback.

    Cutoff = max `page_end` (or `page_start` if page_end missing) across
    all inline sections that carry "theory" in content_types.
    """
    sections = data.get("sections") or []
    excluded = data.get("excluded_sections") or []
    if not excluded:
        return []

    # Find the maximum page touched by any theory-carrying section.
    # Excludes type='chapter' wrappers because PYQ papers (§11.4) have a
    # chapter wrapper marked ["theory"] that holds a tiny header and the
    # whole question paper inside its page range — its page_end is not a
    # meaningful "end of theory" boundary. Only NON-wrapper theory sections
    # define the cutoff.
    last_theory_page = 0

    def walk_theory(nodes):
        nonlocal last_theory_page
        for n in nodes or []:
            if not isinstance(n, dict):
                continue
            ct = n.get("content_types") or []
            ntype = (n.get("type") or "").lower()
            if (
                isinstance(ct, list)
                and "theory" in ct
                and ntype != "chapter"
            ):
                pe = n.get("page_end") or n.get("page_start") or 0
                if isinstance(pe, int) and pe > last_theory_page:
                    last_theory_page = pe
            walk_theory(n.get("subsections") or [])

    walk_theory(sections)

    if last_theory_page == 0:
        # No theory anywhere — pure-Q book. Excluded position rule doesn't apply.
        return []

    out: list[ValidationError] = []
    for ex in excluded:
        if not isinstance(ex, dict):
            continue
        ps = ex.get("page_start")
        if not isinstance(ps, int):
            continue
        if ps < last_theory_page:
            out.append(ValidationError(
                type=ErrorType.MID_CHAPTER_EXCLUDED,
                section_id=ex.get("id"),
                section_title=ex.get("title"),
                severity="error",
                message=(
                    f'Excluded entry "{ex.get("title")}" starts on page '
                    f'{ps}, which is BEFORE the last theory section ends '
                    f'(page {last_theory_page}). `excluded_sections` is '
                    f'for END-OF-CHAPTER content only. Mid-chapter Q-like '
                    f'content (illustrations, in-text questions, mid-chapter '
                    f'banks) must be INLINE Cat A under its preceding theory '
                    f'parent per §8.3 — NEVER in excluded_sections.'
                ),
                context={
                    "excluded_page_start": ps,
                    "last_theory_page": last_theory_page,
                },
            ))
    return out


_PUZZLE_TITLE_RE = re.compile(
    r"""
    \b
    (?:
        crossword s?
        | word \s* (?: puzzle | search ) s?
        | jumble s?
        | sudoku
        | riddle s?
        | brain \s* teaser s?
    )
    \b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _check_puzzle_as_section(data: dict) -> list[ValidationError]:
    """§5.9: Crosswords, word puzzles, word searches, etc. are Cat C inline
    callouts and MUST NEVER appear as their own section in `sections[]` OR
    `excluded_sections[]`. They have no extractable theory/questions —
    emitting them as a section creates a pending row that never gets
    extracted and shows up as a broken empty entry in the UI.
    """
    out: list[ValidationError] = []

    def walk_section(s, parent_id=None):
        if not isinstance(s, dict):
            return
        title = (s.get("title") or "").strip()
        if title and _PUZZLE_TITLE_RE.search(title):
            out.append(ValidationError(
                type=ErrorType.PUZZLE_AS_SECTION,
                section_id=s.get("id"),
                section_title=title,
                severity="error",
                message=(
                    f'Section "{title}" is a puzzle/word-game block — per §5.9 '
                    f'these are Cat C inline callouts, NOT separate sections. '
                    f'Remove this section entry from `sections[]`; the surrounding '
                    f'theory section absorbs the puzzle as inline body.'
                ),
                context={"title": title, "id": s.get("id")},
            ))
        for child in s.get("subsections") or []:
            walk_section(child, s.get("id"))

    for top in data.get("sections") or []:
        walk_section(top)

    def walk_excluded(e):
        if not isinstance(e, dict):
            return
        title = (e.get("title") or "").strip()
        if title and _PUZZLE_TITLE_RE.search(title):
            out.append(ValidationError(
                type=ErrorType.PUZZLE_AS_SECTION,
                section_id=None,
                section_title=title,
                severity="error",
                message=(
                    f'Excluded entry "{title}" is a puzzle/word-game block — per '
                    f'§5.9 these are Cat C inline callouts, NOT excluded sections. '
                    f'Remove this from `excluded_sections[]`.'
                ),
                context={"title": title, "where": "excluded_sections"},
            ))
        for sub in e.get("subsections") or []:
            walk_excluded(sub)

    for ex in data.get("excluded_sections") or []:
        walk_excluded(ex)

    return out


def _is_cat_a(section: dict) -> bool:
    """Cat A = content_types includes 'questions' (or only 'questions')."""
    ct = section.get("content_types") or []
    if not isinstance(ct, list):
        return False
    return "questions" in ct


def _is_cat_b(section: dict) -> bool:
    """Cat B = content_types includes 'theory' (theory-bearing)."""
    ct = section.get("content_types") or []
    if not isinstance(ct, list):
        return False
    return "theory" in ct


def _check_cat_a_nested_under_previous_theory(data: dict) -> list[ValidationError]:
    """Every Cat A subsection MUST nest under the IMMEDIATELY PRECEDING Cat B
    section in document order. If a Cat A is found at a level where a Cat B
    sibling appeared earlier in the same parent's children list, the Cat A
    should have been moved to be a child of that Cat B.

    The deterministic rule walks each parent's children in array order
    (which mirrors document order per §3.4). For every Cat A child found,
    we check whether ANY preceding sibling is Cat B. If yes → the Cat A
    is misplaced (should be nested under that preceding Cat B). If no
    Cat B sibling exists → Cat A is genuinely at this level (e.g., pure-Q
    chapter), allowed.

    NOTE: this is a violation of POSITION-ORDER nesting, not a violation
    of content-type rules. The Cat A's content is fine; only its placement
    in the tree is wrong.
    """
    out: list[ValidationError] = []

    def walk(parent: dict) -> None:
        children = parent.get("subsections") or []
        last_cat_b_idx: int | None = None
        last_cat_b_id: str | None = None
        last_cat_b_title: str | None = None
        for i, child in enumerate(children):
            if not isinstance(child, dict):
                continue
            if _is_cat_b(child):
                last_cat_b_idx = i
                last_cat_b_id = child.get("id")
                last_cat_b_title = child.get("title", "")
                # Recurse into Cat B (its own children get checked too)
                walk(child)
                continue
            if _is_cat_a(child):
                if last_cat_b_idx is not None:
                    # Violation — Cat A at this level but a Cat B sibling
                    # already exists. It should nest under that Cat B.
                    out.append(ValidationError(
                        type=ErrorType.CAT_A_NOT_NESTED_UNDER_PREVIOUS_THEORY,
                        section_id=child.get("id"),
                        section_title=child.get("title", ""),
                        # SCHEMA Rebalance — downgraded to warning. The
                        # cat_a_nesting sanitizer (schema_cat_a_nesting.py)
                        # auto-repairs this deterministically before
                        # validation in the production path, so the
                        # validator firing here means the sanitizer's
                        # post-state still has a stray Cat A under root —
                        # informational, never blocking.
                        severity="warning",
                        message=(
                            f'Cat A section "{child.get("title")}" (id={child.get("id")!r}) '
                            f'is at level <{parent.get("id") or "ROOT"}> but a preceding '
                            f'Cat B sibling "{last_cat_b_title}" (id={last_cat_b_id!r}) '
                            f'exists earlier in this parent. Cat A must nest UNDER its '
                            f'immediately preceding Cat B in document order. Move this '
                            f'Cat A to be a child of "{last_cat_b_title}" (id={last_cat_b_id!r}).'
                        ),
                        context={
                            "cat_a_id": child.get("id"),
                            "cat_a_title": child.get("title"),
                            "should_nest_under_id": last_cat_b_id,
                            "should_nest_under_title": last_cat_b_title,
                            "parent_id": parent.get("id"),
                        },
                    ))
                # Recurse into Cat A's children too (rare but possible)
                walk(child)
                continue
            # Other types (e.g., chapter wrapper, mixed-section types)
            # walk through without affecting the last_cat_b tracking.
            walk(child)

    # Walk top-level sections. The fake "root" container holds them as siblings.
    walk({"id": "ROOT", "subsections": data.get("sections") or []})
    return out


def _check_cat_a_parent_page_mismatch(data: dict) -> list[ValidationError]:
    """Every Cat A subsection's `page_start` MUST fall within its parent
    theory (Cat B) section's `[page_start, page_end]` range.

    Catches the cross-page failure mode where Gemini nests Example 9.5
    under §9.2 (p10-15) just because §9.2 mentions "see Example 9.5",
    even though Example 9.5's printed heading lives on page 27 inside
    §9.7's range.

    Also catches same-page misnestings where Cat A's page_start sits
    outside the immediate Cat B parent's page range (e.g. parent ends at
    p3 but Cat A starts at p4 because it really belongs to the next
    theory section).

    Skip cases:
      * Cat A has no parent in the main tree (top-level)
      * Parent is not Cat B (e.g. Cat A nested under Cat A wrapper — caller
        responsibility, different rule)
      * Either parent or child has non-integer page_start/page_end (rule
        1 handles those)
      * Excluded section (only `sections[]` tree matters here)

    The error context surfaces which theory section's range WOULD contain
    the Cat A's page_start (if any), so the corrective fragment can
    instruct Gemini precisely.
    """
    out: list[ValidationError] = []

    sections = data.get("sections") or []

    # Build a flat list of all (Cat B) theory sections with their page ranges
    # for the "would-be parent" hint.
    theory_index: list[tuple[dict, int, int, int]] = []  # (sec, ps, pe, depth)

    def index_theory(nodes, depth):
        for n in nodes or []:
            if not isinstance(n, dict):
                continue
            if _is_cat_b(n):
                ps = n.get("page_start")
                pe = n.get("page_end")
                if isinstance(ps, int) and not isinstance(ps, bool):
                    if not (isinstance(pe, int) and not isinstance(pe, bool)):
                        pe = ps
                    theory_index.append((n, ps, pe, depth))
            index_theory(n.get("subsections") or [], depth + 1)

    index_theory(sections, 0)

    def find_would_be_parent(child_ps: int, current_parent_id: str | None):
        """Return the deepest theory section whose range contains child_ps.
        Excludes the current_parent itself. Returns (id, title, range) or None."""
        candidates = [
            (s, ps, pe, d)
            for (s, ps, pe, d) in theory_index
            if ps <= child_ps <= pe and s.get("id") != current_parent_id
        ]
        if not candidates:
            return None
        # Deepest wins
        candidates.sort(key=lambda c: -c[3])
        s, ps, pe, _ = candidates[0]
        return (s.get("id"), s.get("title"), [ps, pe])

    def walk(nodes, parent):
        for n in nodes or []:
            if not isinstance(n, dict):
                continue
            if _is_cat_a(n) and parent is not None and _is_cat_b(parent):
                c_ps = n.get("page_start")
                p_ps = parent.get("page_start")
                p_pe = parent.get("page_end")
                if (
                    isinstance(c_ps, int) and not isinstance(c_ps, bool)
                    and isinstance(p_ps, int) and not isinstance(p_ps, bool)
                    and isinstance(p_pe, int) and not isinstance(p_pe, bool)
                ):
                    if not (p_ps <= c_ps <= p_pe):
                        wb = find_would_be_parent(c_ps, parent.get("id"))
                        wb_part = ""
                        if wb is not None:
                            wb_part = (
                                f' The theory section whose range '
                                f'contains page {c_ps} is "{wb[1]}" '
                                f'(id={wb[0]!r}, pages {wb[2][0]}-{wb[2][1]}).'
                            )
                        out.append(ValidationError(
                            type=ErrorType.CAT_A_PARENT_PAGE_MISMATCH,
                            section_id=n.get("id"),
                            section_title=n.get("title"),
                            # SCHEMA Rebalance — downgraded to warning.
                            # The cat_a_nesting sanitizer (positional pass)
                            # auto-repairs page-mismatch parent assignments
                            # using PDF text. Validator firing here is
                            # informational, not a retry trigger.
                            severity="warning",
                            message=(
                                f'Cat A "{n.get("title")}" has page_start='
                                f'{c_ps} but its current parent theory '
                                f'"{parent.get("title")}" (id='
                                f'{parent.get("id")!r}) only covers pages '
                                f'{p_ps}-{p_pe}.{wb_part} '
                                f'A Cat A must nest under the theory section '
                                f'whose page range contains the Cat A\'s own '
                                f'printed heading (per §3.2.6).'
                            ),
                            context={
                                "cat_a_id": n.get("id"),
                                "cat_a_title": n.get("title"),
                                "cat_a_page_start": c_ps,
                                "parent_id": parent.get("id"),
                                "parent_title": parent.get("title"),
                                "parent_pages": [p_ps, p_pe],
                                "would_be_parent_id": wb[0] if wb else None,
                                "would_be_parent_title": wb[1] if wb else None,
                                "would_be_parent_pages": wb[2] if wb else None,
                            },
                        ))
            walk(n.get("subsections") or [], n)

    walk(sections, None)
    return out


def _check_schema_page_coverage(data: dict) -> list[ValidationError]:
    """Whole-schema invariant: every page in [1..total_pages] must appear
    in at least one section (anywhere in the sections[] tree) OR in
    excluded_sections[]. Differs from Rule 6 (PAGE_COVERAGE_GAP, a
    warning that only counts LEAF coverage) in two ways:

      1. ERROR severity — blocks acceptance, drives corrective retry.
         Missing pages almost always mean Gemini missed headings (see
         §4.0 PASS 2 self-check). The downstream extractor then has no
         section anchored on those pages, so any questions/figures there
         go off-schema (the user's Modern Physics pp.37-46 case).

      2. Container coverage counts — a chapter wrapper [1..30] with no
         leaf children for pages 28-30 still "covers" them at the
         container level. Rule 6 flags that as a warning; this rule
         does not. We're catching the harder failure: pages absent
         from the schema entirely.

    Reads `total_pages` from `data` (schema's own top-level field). If
    missing/invalid → skip (other rules / preflight handle that).
    """
    total_pages = data.get("total_pages")
    if not isinstance(total_pages, int) or isinstance(total_pages, bool):
        return []
    if total_pages <= 0:
        return []

    def _is_int(v):
        return isinstance(v, int) and not isinstance(v, bool)

    covered: set[int] = set()

    def _walk(nodes):
        for n in nodes or []:
            if not isinstance(n, dict):
                continue
            ps = n.get("page_start")
            pe = n.get("page_end")
            if _is_int(ps) and _is_int(pe) and ps <= pe:
                for p in range(ps, pe + 1):
                    if 1 <= p <= total_pages:
                        covered.add(p)
            elif _is_int(ps):
                if 1 <= ps <= total_pages:
                    covered.add(ps)
            _walk(n.get("subsections") or [])

    _walk(data.get("sections") or [])
    _walk(data.get("excluded_sections") or [])

    all_pages = set(range(1, total_pages + 1))
    missing = sorted(all_pages - covered)
    if not missing:
        return []

    # Group consecutive missing pages into ranges for the message.
    ranges: list[tuple[int, int]] = []
    run_start = missing[0]
    run_end = missing[0]
    for p in missing[1:]:
        if p == run_end + 1:
            run_end = p
        else:
            ranges.append((run_start, run_end))
            run_start = run_end = p
    ranges.append((run_start, run_end))

    range_strs = [f"{a}" if a == b else f"{a}-{b}" for a, b in ranges]
    return [ValidationError(
        type=ErrorType.SCHEMA_PAGE_COVERAGE_INCOMPLETE,
        section_id=None,
        section_title=None,
        # SCHEMA Rebalance — downgraded to warning. Many legitimate PDFs
        # have un-covered cover pages, blanks, copyright pages, etc., and
        # the page-clamping sanitizer + cross-check_section_pages already
        # exercise stronger guarantees. Treating page coverage gaps as
        # blocking forces retries on books that are actually fine.
        severity="warning",
        message=(
            f"Schema page coverage incomplete. Missing pages: "
            f"{', '.join(range_strs)}. Every page of the PDF must be "
            f"covered by either sections[] or excluded_sections[]."
        ),
        context={
            "missing_pages": missing,
            "missing_ranges": [list(r) for r in ranges],
            "total_pages": total_pages,
        },
    )]


# ─── ENTRY POINT ───────────────────────────────────────────────────


def validate_schema(
    data: dict,
    *,
    pdf_total_pages: int | None = None,
) -> ValidationResult:
    """Run all enabled validation rules against the parsed schema dict.

    Parameters
    ----------
    data : dict
        Parsed schema (Gemini output after parse_json + sanitize, with
        UUIDs assigned). Pydantic validation can be lossy on partials;
        we validate the raw dict before constructing BookSchema.
    pdf_total_pages : int, optional
        Total page count from preflight (or pymupdf). Required for
        PAGE_OUT_OF_BOUNDS rule; rule is skipped if None.

    Returns
    -------
    ValidationResult with `errors` (block schema acceptance, drive
    corrective retry) and `warnings` (surface to user, don't block).
    """
    errors: list[ValidationError] = []
    warnings: list[ValidationError] = []

    # Track which sibling-lists we've checked for overlap so we don't
    # re-check the same siblings once per section in that group.
    seen_sibling_lists: set[int] = set()

    for section, parent, siblings, is_excluded in _iter_with_parent(data):
        # Rules that apply to BOTH sections and excluded entries
        # (excluded_sections have page_start, page_end, expected_question_count,
        # subsections — these page-shape rules are universal).
        for err in _check_non_integer_page(section, []):
            (errors if err.severity == "error" else warnings).append(err)
        for err in _check_inverted_range(section, []):
            (errors if err.severity == "error" else warnings).append(err)
        for err in _check_page_out_of_bounds(
            section, [], pdf_total_pages=pdf_total_pages
        ):
            (errors if err.severity == "error" else warnings).append(err)
        # Day 6 — parent containment (applies within excluded subsections too)
        for err in _check_page_outside_parent(section, parent):
            (errors if err.severity == "error" else warnings).append(err)
        # Day 6 — leaf page presence
        for err in _check_missing_leaf_page(section, []):
            (errors if err.severity == "error" else warnings).append(err)

        # Rules that apply ONLY to sections (skipped for excluded entries
        # because ExcludedSection schema doesn't have type/content_types,
        # and Cat A-end-of-chapter / individual-Q-as-section rules are
        # meaningless once the section is already in excluded_sections).
        if not is_excluded:
            # Day 4 — Q4-as-section (only applies to inline sections)
            for err in _check_individual_question_as_section(section, []):
                (errors if err.severity == "error" else warnings).append(err)
            # Day 6 — type enum (only Section objects have `type` field)
            for err in _check_invalid_type(section, []):
                (errors if err.severity == "error" else warnings).append(err)
            # Day 7 — content_types vocabulary (only Section objects)
            for err in _check_invalid_content_types(section, []):
                (errors if err.severity == "error" else warnings).append(err)
            # Day 7 — Cat A at end belongs in excluded_sections (only
            # applies to inline sections; if already excluded, can't fire)
            for err in _check_cat_a_at_end_not_excluded(section, parent, siblings):
                (errors if err.severity == "error" else warnings).append(err)

        # Sibling-pair rule: check ONCE per sibling list (not once per
        # section in the list).
        siblings_id = id(siblings)
        if siblings_id not in seen_sibling_lists:
            seen_sibling_lists.add(siblings_id)
            for err in _check_sibling_page_overlap(siblings):
                (errors if err.severity == "error" else warnings).append(err)

    # Whole-schema rules (run once, not per section)
    # Day 6 — page coverage gap (warning)
    for err in _check_page_coverage_gap(data, pdf_total_pages):
        (errors if err.severity == "error" else warnings).append(err)
    # Day 7 — catastrophic empty schema
    for err in _check_empty_placeholder(data, pdf_total_pages):
        (errors if err.severity == "error" else warnings).append(err)
    # Theory Unit 1 — excluded_sections hard rules (§0.5 #4)
    for err in _check_title_duplicate_across_arrays(data):
        (errors if err.severity == "error" else warnings).append(err)
    for err in _check_mid_chapter_excluded(data):
        (errors if err.severity == "error" else warnings).append(err)
    # Theory Unit 9 follow-up — §5.9 puzzle blocks must not be sections
    for err in _check_puzzle_as_section(data):
        (errors if err.severity == "error" else warnings).append(err)
    # Cat A nesting rule — every Cat A must nest under preceding Cat B sibling
    for err in _check_cat_a_nested_under_previous_theory(data):
        (errors if err.severity == "error" else warnings).append(err)
    # Positional Cat A — parent page range must contain Cat A's page_start
    for err in _check_cat_a_parent_page_mismatch(data):
        (errors if err.severity == "error" else warnings).append(err)
    # Whole-schema page coverage — every PDF page must be in sections[] or excluded_sections[]
    for err in _check_schema_page_coverage(data):
        (errors if err.severity == "error" else warnings).append(err)

    return ValidationResult(
        is_valid=not errors,
        errors=errors,
        warnings=warnings,
    )


__all__ = [
    "ErrorType",
    "ValidationError",
    "ValidationResult",
    "validate_schema",
]
