"""Corrective prompt library — turns validation errors into Gemini
re-instructions.

When `schema_validator.validate_schema()` returns errors, the schema
builder doesn't just retry with the same prompt (today's broken
behaviour). It builds a CORRECTIVE prompt that names each specific
error and asks Gemini to fix exactly that.

Example: validator catches NON_INTEGER_PAGE on section "EXAMPLE 8.1"
with page_start="9.18". Corrective prompt appended to base schema
prompt becomes:

    ## YOUR PREVIOUS ATTEMPT HAD ERRORS — FIX THESE

    Section "EXAMPLE 8.1" had page_start="9.18" which is not an integer.
    Pages must be the physical printed page number where the section's
    content appears (an integer between 1 and 50). Label numbers in
    section titles (like "9.18" in "EXAMPLE 9.18") are NEVER page numbers.

    Re-emit the schema. For this section, set page_start to the actual
    physical page where "EXAMPLE 8.1" is printed.

This makes Gemini fix the specific issue instead of guessing.

Pure-string functions — no Gemini call, no DB I/O. The caller
(schema_builder) appends the output to the base prompt and re-runs
Gemini.
"""

from __future__ import annotations

from app.services.schema_validator import ErrorType, ValidationError


# ─── PER-ERROR CORRECTIVE FRAGMENTS ────────────────────────────────


def _fragment_non_integer_page(err: ValidationError, total_pages: int | None) -> str:
    """NON_INTEGER_PAGE — Gemini emitted a non-integer page value."""
    field = err.context.get("field", "page")
    value = err.context.get("value", "?")
    title = err.section_title or "<unknown section>"
    bounds = f"between 1 and {total_pages}" if total_pages else "≥ 1"
    return (
        f'- Section "{title}" had {field}={value!r}, which is NOT a valid '
        f"integer page number. Pages must be physical printed page "
        f"numbers (integers {bounds}). "
        f"NEVER use the section's label number (e.g. \"9.18\" in "
        f"\"EXAMPLE 9.18\") as a page — that's the example identifier, "
        f"not a page. Re-emit this section with the correct physical "
        f"printed page number."
    )


def _fragment_inverted_range(err: ValidationError, _total_pages: int | None) -> str:
    """INVERTED_RANGE — page_start > page_end."""
    ps = err.context.get("page_start")
    pe = err.context.get("page_end")
    title = err.section_title or "<unknown section>"
    return (
        f'- Section "{title}" had page_start={ps} > page_end={pe}. '
        f"A section's pages are contiguous: page_start must be ≤ page_end. "
        f"Re-emit this section with the correct order (start is the FIRST "
        f"page, end is the LAST page where the section's content appears)."
    )


def _fragment_page_out_of_bounds(err: ValidationError, total_pages: int | None) -> str:
    """PAGE_OUT_OF_BOUNDS — page number exceeds PDF length."""
    field = err.context.get("field", "page")
    value = err.context.get("value", "?")
    total = err.context.get("pdf_total_pages", total_pages)
    title = err.section_title or "<unknown section>"
    return (
        f'- Section "{title}" had {field}={value}, but the PDF only has '
        f"{total} pages. Page numbers cannot exceed the total. "
        f"Re-emit this section with the correct physical page number "
        f"(between 1 and {total})."
    )


def _fragment_individual_question_as_section(
    err: ValidationError, _total_pages: int | None
) -> str:
    """INDIVIDUAL_QUESTION_AS_SECTION — Gemini wrongly promoted a single
    numbered MCQ/question into its own schema entry."""
    title = err.section_title or "<unknown section>"
    page = err.context.get("page_start")
    page_hint = (
        f" (with page_start={page}, which is likely the question's "
        f"NUMBER not its page)" if page is not None else ""
    )
    return (
        f'- Section "{title}" looks like an individual numbered '
        f"question wrongly emitted as a standalone schema section"
        f"{page_hint}. "
        f"Individual questions inside a question bank are NOT "
        f"separate schema entries — they're counted in the parent "
        f"bank's `expected_question_count` field. "
        f"REMOVE this section entirely. Increment the parent question "
        f"bank's expected_question_count instead. "
        f"NEVER emit ids like 'practice-q-4' or titles like 'Question 4' "
        f"or '4' for individual questions."
    )


def _fragment_page_outside_parent(
    err: ValidationError, _total_pages: int | None
) -> str:
    """PAGE_OUTSIDE_PARENT — child's pages extend outside parent's range."""
    title = err.section_title or "<unknown section>"
    parent = err.context.get("parent_title", "<unknown parent>")
    c_start = err.context.get("child_start")
    p_start = err.context.get("parent_start")
    c_end = err.context.get("child_end")
    p_end = err.context.get("parent_end")
    if c_start is not None and p_start is not None:
        # before-parent error
        return (
            f'- Section "{title}" starts on page {c_start} but its '
            f'parent "{parent}" only starts on page {p_start}. '
            f"A child section must start at OR after its parent. "
            f"Either fix the child's page_start to be ≥ {p_start}, "
            f"or extend parent's page_start to ≤ {c_start} so it "
            f"truly covers the child."
        )
    # after-parent error
    return (
        f'- Section "{title}" ends on page {c_end} but its parent '
        f'"{parent}" only ends on page {p_end}. '
        f"A child section must end at OR before its parent. "
        f"Either fix the child's page_end to be ≤ {p_end}, "
        f"or extend parent's page_end to ≥ {c_end}."
    )


def _fragment_sibling_page_overlap(
    err: ValidationError, _total_pages: int | None
) -> str:
    """SIBLING_PAGE_OVERLAP — two sibling sections claim overlapping pages."""
    a_title = err.context.get("section_a_title", "<A>")
    b_title = err.context.get("section_b_title", "<B>")
    a_pages = err.context.get("section_a_pages", [])
    b_pages = err.context.get("section_b_pages", [])
    overlap = err.context.get("overlap", [])
    return (
        f'- Sibling sections "{a_title}" (pages {a_pages[0]}-{a_pages[1]}) '
        f'and "{b_title}" (pages {b_pages[0]}-{b_pages[1]}) both claim '
        f'pages {overlap[0]}-{overlap[1]}. Sections cover different '
        f"parts of the PDF — they shouldn't overlap. Fix one of their "
        f"page ranges so they're adjacent (e.g. A ends at page 5, B "
        f"starts at page 6) or just touch on one boundary page."
    )


def _fragment_page_coverage_gap(
    err: ValidationError, _total_pages: int | None
) -> str:
    """PAGE_COVERAGE_GAP — pages not covered by any leaf section (warning).

    This is a WARNING-severity error, but we include a corrective
    fragment in case the caller chooses to feed it back to Gemini for
    completeness.
    """
    ranges = err.context.get("missing_ranges", [])
    range_strs = [f"{a}" if a == b else f"{a}-{b}" for a, b in ranges]
    pretty = ", ".join(range_strs)
    return (
        f"- The schema doesn't cover these pages with any leaf "
        f"section: {pretty}. If those pages contain theory or "
        f"questions, add sections for them. If they're cover/blank/"
        f"copyright/index, leave as-is — those are legitimately "
        f"uncovered."
    )


def _fragment_invalid_type(
    err: ValidationError, _total_pages: int | None
) -> str:
    """INVALID_TYPE — section type not in canonical enum (or MISSING)."""
    title = err.section_title or "<unknown section>"
    bad = err.context.get("value")
    level = err.context.get("level")

    # Special-case: missing `type` field — most common production failure.
    # Use level (if known) to suggest the right canonical value.
    if bad is None:
        if level == 1:
            suggested = "\"chapter\""
        elif level == 2:
            suggested = "\"section\""
        elif level and level >= 3:
            suggested = "\"subsection\""
        else:
            suggested = "\"subsection\" (or \"section\" / \"chapter\" by level)"
        return (
            f'- Section "{title}" is MISSING the `type` field entirely. '
            f"This field is REQUIRED on every section node. "
            f"Given level={level!r}, set `type`={suggested}. "
            f"(Level 1 → \"chapter\"; level 2 → \"section\"; level 3+ → "
            f"\"subsection\".) Re-emit this section with `type` populated."
        )

    return (
        f'- Section "{title}" has type={bad!r}. The `type` field must be '
        f"EXACTLY one of these three lowercase values: "
        f"\"chapter\" | \"section\" | \"subsection\". Nothing else is "
        f"accepted. FORBIDDEN values include: \"subsubsection\", "
        f"\"topic\", \"subtopic\", \"unit\", \"part\", \"sub-section\", "
        f"\"sub_section\", \"lesson\", \"module\", \"Chapter\" "
        f"(capitalised). Re-emit with the correct canonical type "
        f"(level 1 → \"chapter\"; level 2 → \"section\"; level 3+ → "
        f"\"subsection\")."
    )


def _fragment_missing_leaf_page(
    err: ValidationError, _total_pages: int | None
) -> str:
    """MISSING_LEAF_PAGE — leaf section has no page_start, extraction can't run."""
    title = err.section_title or "<unknown section>"
    return (
        f'- Section "{title}" has no subsections AND no page_start. '
        f"Leaf sections require a physical page number — content "
        f"extraction needs to know which page to look at. "
        f"Either add page_start (the page where this section begins) "
        f"OR add subsections (making it a container that covers "
        f"its children's pages). NEVER leave a leaf with page_start "
        f"= null — extraction will silently fail."
    )


def _fragment_invalid_content_types(
    err: ValidationError, _total_pages: int | None
) -> str:
    """INVALID_CONTENT_TYPES — content_types is malformed, missing, or
    uses unknown/forbidden values."""
    title = err.section_title or "<unknown section>"
    value = err.context.get("value")

    # Special-case: missing field (None) — most common production failure.
    # Tie the fix to the section's title so Gemini gets a concrete answer.
    if value is None:
        bank_keywords = (
            "thinking", "practice", "mcq", "exercise", "drill",
            "problem", "questions", "question bank", "assessment",
            "test", "review", "level", "workout",
        )
        t_lower = (title or "").lower()
        is_bank = any(k in t_lower for k in bank_keywords)
        suggested = "[\"questions\"]" if is_bank else "[\"theory\"]"
        return (
            f'- Section "{title}" is MISSING the `content_types` field entirely. '
            f"This field is REQUIRED on every section node. "
            f"Given the title \"{title}\", set `content_types`={suggested}. "
            f"(Question-bank style titles like Classical Thinking / Critical "
            f"Thinking / Practice Questions / MCQ Bank / Exercise / Drill "
            f"get `[\"questions\"]`. Theory titles get `[\"theory\"]`.) "
            f"Re-emit this section with `content_types` populated."
        )

    return (
        f'- Section "{title}" has content_types={value!r}, which is invalid. '
        f"Valid content_types arrays (lowercase, exactly one of these four):\n"
        f"    [\"theory\"]            — pure theory section\n"
        f"    [\"questions\"]         — pure question / exercise / example container\n"
        f"    [\"theory\", \"figures\"]   — theory section that is image-heavy\n"
        f"    [\"questions\", \"figures\"] — question section with images\n\n"
        f"FORBIDDEN combinations (these will be rejected):\n"
        f"    [\"theory\", \"questions\"]   — mixed; a section is EITHER\n"
        f"        theory OR questions in its own content_types. If a theory\n"
        f"        section contains Cat A items (Examples, Exercises) inside,\n"
        f"        the parent stays [\"theory\"] and Cat A children carry\n"
        f"        their own [\"questions\"] separately.\n"
        f"    [\"theory\", \"questions\", \"figures\"]  — also forbidden mixed.\n\n"
        f"FORBIDDEN tokens: `question` (singular — use `questions`), "
        f"`examples` / `solved_examples` / `worked_examples` (all → `questions`), "
        f"`tables` / `table` (→ `figures`), `summary` / `activity` / `biography` / "
        f"`fun_fact` / `learning_objectives` / `note` / `exercise` (→ canonical "
        f"`theory` or `questions`). Use only the 3 canonical lowercase tokens."
    )


def _fragment_cat_a_at_end_not_excluded(
    err: ValidationError, _total_pages: int | None
) -> str:
    """CAT_A_AT_END_NOT_EXCLUDED — chapter-end Cat A bank wrongly nested
    in main sections tree instead of excluded_sections."""
    title = err.section_title or "<unknown section>"
    reason = err.context.get("reason", "")
    parent = err.context.get("parent_title", "")

    if reason == "standalone_help":
        return (
            f'- Section "{title}" is a standalone help-section title '
            f"(hints, solutions, answer keys, answers). These ALWAYS "
            f"belong in the schema's excluded_sections array (flat "
            f"top-level list), never nested as a subsection of any "
            f"theory parent. Move \"{title}\" out of the sections "
            f"tree and into excluded_sections with content_types="
            f"[\"questions\"]."
        )
    # Positional case
    parent_hint = f' (currently nested under "{parent}")' if parent else ""
    return (
        f'- Section "{title}" is a Cat A bank at the END of its chapter'
        f"{parent_hint} — no theory section follows it at the same "
        f"level. End-of-chapter banks belong in excluded_sections "
        f"(flat top-level array), not nested in the main sections "
        f"tree. Move \"{title}\" to excluded_sections with "
        f"content_types=[\"questions\"]. Inline numbered items "
        f"(Example 1.1, Exercise 8.3, Problem 5.1 — anything with "
        f"X.Y decimal) stay inline in their parent's subsections."
    )


def _fragment_empty_placeholder(
    err: ValidationError, _total_pages: int | None
) -> str:
    """EMPTY_PLACEHOLDER — schema is empty but PDF has content."""
    total = err.context.get("pdf_total_pages", "?")
    return (
        f"- The schema you returned is COMPLETELY EMPTY (zero "
        f"sections AND zero excluded_sections), but the PDF has "
        f"{total} pages of content. This is forbidden. Re-emit "
        f"the schema with at least one section. If the PDF truly "
        f"has no instructional content (covers, blanks only), "
        f"populate extraction_notes explaining why. Otherwise, "
        f"identify the chapter / sections / banks present and "
        f"emit them."
    )


def _fragment_title_duplicate_across_arrays(
    err: ValidationError, _total_pages: int | None
) -> str:
    """TITLE_DUPLICATE_ACROSS_ARRAYS — per §0.5 #4c, a title cannot live in
    both sections[] and excluded_sections[]."""
    title = err.context.get("title", err.section_title or "?")
    inline_page = err.context.get("inline_page", "?")
    excluded_page = err.context.get("excluded_page", "?")
    return (
        f"- Title \"{title}\" appears in BOTH `sections[]` "
        f"(inline at page {inline_page}) AND `excluded_sections[]` "
        f"(at page {excluded_page}). Per §0.5 #4(c), each piece of "
        f"content lives in EXACTLY ONE array. Fix this: if the title "
        f"refers to an inline section (theory or mid-chapter Cat A), "
        f"REMOVE the excluded duplicate. If the excluded entry is a "
        f"distinct end-of-chapter question bank that happens to test "
        f"that section, RENAME it (e.g. \"Practice Set — {title}\" or "
        f"\"Questions on {title}\") — never use the bare inline title "
        f"verbatim in `excluded_sections`."
    )


def _fragment_puzzle_as_section(
    err: ValidationError, _total_pages: int | None
) -> str:
    """PUZZLE_AS_SECTION — §5.9 violation."""
    title = err.section_title or "?"
    return (
        f"- Section \"{title}\" is a puzzle / word-game / crossword block. "
        f"Per §5.9, puzzle blocks are CAT C INLINE callouts — they have no "
        f"extractable theory and no extractable questions. REMOVE this "
        f"section entry entirely from both `sections[]` and `excluded_sections[]`. "
        f"The surrounding theory section absorbs the puzzle as inline body "
        f"automatically. Do NOT create any schema entry for crosswords, word "
        f"puzzles, jumbles, sudoku, riddles, etc."
    )


def _fragment_cat_a_not_nested(
    err: ValidationError, _total_pages: int | None
) -> str:
    """CAT_A_NOT_NESTED_UNDER_PREVIOUS_THEORY — Cat A placed as sibling of
    preceding Cat B instead of nested under it."""
    ctx = err.context or {}
    cat_a_title = ctx.get("cat_a_title") or err.section_title or "?"
    cat_a_id = ctx.get("cat_a_id") or err.section_id or "?"
    target_title = ctx.get("should_nest_under_title") or "?"
    target_id = ctx.get("should_nest_under_id") or "?"
    return (
        f'- Cat A subsection "{cat_a_title}" (id={cat_a_id!r}) is placed as '
        f'a SIBLING of theory sections at the same level. Per §3.2, every '
        f'Cat A subsection MUST nest under the immediately preceding Cat B '
        f'section in document order. Move "{cat_a_title}" to be a CHILD '
        f'of "{target_title}" (id={target_id!r}) — not a sibling.'
    )


def _fragment_cat_a_parent_page_mismatch(
    err: ValidationError, _total_pages: int | None
) -> str:
    """CAT_A_PARENT_PAGE_MISMATCH — Cat A's page_start outside parent's
    page range. Suggest the correct theory parent identified by the
    validator's would-be-parent search."""
    ctx = err.context or {}
    cat_a_title = ctx.get("cat_a_title") or err.section_title or "?"
    cat_a_id = ctx.get("cat_a_id") or err.section_id or "?"
    cat_a_page = ctx.get("cat_a_page_start", "?")
    parent_title = ctx.get("parent_title", "?")
    parent_pages = ctx.get("parent_pages", ["?", "?"])
    wb_id = ctx.get("would_be_parent_id")
    wb_title = ctx.get("would_be_parent_title")
    wb_pages = ctx.get("would_be_parent_pages")
    if wb_id and wb_title:
        wb_hint = (
            f' The theory section whose page range contains page '
            f'{cat_a_page} is "{wb_title}" (id={wb_id!r}, pages '
            f'{wb_pages[0]}-{wb_pages[1]}). MOVE "{cat_a_title}" to be a '
            f'CHILD of "{wb_title}" instead of "{parent_title}".'
        )
    else:
        wb_hint = (
            f' No other theory section\'s page range contains page '
            f'{cat_a_page} either — re-check the Cat A\'s page_start: it '
            f'must be the physical page where the Cat A\'s printed heading '
            f'(e.g. "EXAMPLE 9.5") appears, NOT the page where a text '
            f'reference like "see Example 9.5" appears.'
        )
    return (
        f'- Cat A "{cat_a_title}" (id={cat_a_id!r}) has page_start='
        f'{cat_a_page}, but its current parent theory "{parent_title}" '
        f'only covers pages {parent_pages[0]}-{parent_pages[1]}. Per '
        f'§3.2.6, a Cat A must nest under whichever theory section\'s '
        f'page range contains the Cat A\'s OWN printed heading — NEVER '
        f'a text reference position.{wb_hint}'
    )


def _fragment_mid_chapter_excluded(
    err: ValidationError, _total_pages: int | None
) -> str:
    """MID_CHAPTER_EXCLUDED — per §0.5 #4b, every excluded entry must
    appear AFTER the last theory section in the PDF."""
    title = err.section_title or "?"
    ex_page = err.context.get("excluded_page_start", "?")
    last_theory = err.context.get("last_theory_page", "?")
    return (
        f"- Excluded entry \"{title}\" starts on page {ex_page}, which "
        f"is BEFORE the last theory section ends (page {last_theory}). "
        f"Per §0.5 #4(b), `excluded_sections` is for END-OF-CHAPTER "
        f"content ONLY. Mid-chapter Q-like content (illustrations, "
        f"in-text questions, mid-chapter exercises, banks sandwiched "
        f"between theory blocks) MUST be INLINE Cat A under its preceding "
        f"theory parent per §8.3 — never demoted to excluded_sections. "
        f"Move \"{title}\" into `sections[]` as a Cat A subsection of "
        f"the theory section that immediately precedes it in the PDF."
    )


def _fragment_schema_page_coverage_incomplete(
    err: ValidationError, _total_pages: int | None
) -> str:
    """SCHEMA_PAGE_COVERAGE_INCOMPLETE — pages from [1..total_pages] are
    absent from both sections[] and excluded_sections[].

    Almost always means Gemini missed headings on those pages (the
    Modern Physics pp.37-46 failure mode). Cite §4.0 (heading
    enumeration two-pass scan) and tell Gemini to re-scan the gap.
    """
    ranges = err.context.get("missing_ranges", [])
    total = err.context.get("total_pages", "?")
    range_strs = [f"{a}" if a == b else f"{a}-{b}" for a, b in ranges]
    pretty = ", ".join(range_strs) if range_strs else "?"
    # Build a "pages X-Y" sentence for the re-scan instruction
    if len(ranges) == 1 and ranges[0][0] != ranges[0][1]:
        rescan = f"pages {ranges[0][0]}-{ranges[0][1]}"
    elif len(ranges) == 1:
        rescan = f"page {ranges[0][0]}"
    else:
        rescan = f"pages {pretty}"
    return (
        f"- Schema page coverage is INCOMPLETE. The PDF has "
        f"{total} pages, but the following pages do not appear in "
        f"sections[] OR excluded_sections[]: {pretty}. Per §4.0 "
        f"(HEADING ENUMERATION), you almost certainly missed headings "
        f"on {rescan}. Re-scan {rescan} for content — look for "
        f"unnumbered theory sub-headings, end-of-chapter banks "
        f"(JEE-NEET Wing, Practice Set, etc.), and numbered headings "
        f"that lack obvious visual distinction. Add them to "
        f"sections[] (theory or inline Cat A) or excluded_sections[] "
        f"(end-of-chapter Q-banks/help) as appropriate. Every page of "
        f"the PDF must be covered."
    )


# ─── DISPATCH TABLE ────────────────────────────────────────────────

# Adding a new ErrorType requires adding a matching fragment here.
# If missing, the corrector raises — better to fail loudly than to
# silently skip an error class.
_FRAGMENT_BUILDERS = {
    # Day 3
    ErrorType.NON_INTEGER_PAGE: _fragment_non_integer_page,
    ErrorType.INVERTED_RANGE: _fragment_inverted_range,
    ErrorType.PAGE_OUT_OF_BOUNDS: _fragment_page_out_of_bounds,
    # Day 4
    ErrorType.INDIVIDUAL_QUESTION_AS_SECTION: _fragment_individual_question_as_section,
    # Day 6
    ErrorType.PAGE_OUTSIDE_PARENT: _fragment_page_outside_parent,
    ErrorType.SIBLING_PAGE_OVERLAP: _fragment_sibling_page_overlap,
    ErrorType.PAGE_COVERAGE_GAP: _fragment_page_coverage_gap,
    ErrorType.INVALID_TYPE: _fragment_invalid_type,
    ErrorType.MISSING_LEAF_PAGE: _fragment_missing_leaf_page,
    # Day 7 — validator now feature-complete (12 rules total)
    ErrorType.INVALID_CONTENT_TYPES: _fragment_invalid_content_types,
    ErrorType.CAT_A_AT_END_NOT_EXCLUDED: _fragment_cat_a_at_end_not_excluded,
    ErrorType.EMPTY_PLACEHOLDER: _fragment_empty_placeholder,
    # Theory Unit 1 — excluded_sections hard rules (§0.5 #4)
    ErrorType.TITLE_DUPLICATE_ACROSS_ARRAYS: _fragment_title_duplicate_across_arrays,
    ErrorType.MID_CHAPTER_EXCLUDED: _fragment_mid_chapter_excluded,
    # Theory Unit 9 follow-up — §5.9 puzzle prohibition
    ErrorType.PUZZLE_AS_SECTION: _fragment_puzzle_as_section,
    # Cat A nesting rule
    ErrorType.CAT_A_NOT_NESTED_UNDER_PREVIOUS_THEORY: _fragment_cat_a_not_nested,
    # Positional Cat A parent page-range mismatch (§3.2.6)
    ErrorType.CAT_A_PARENT_PAGE_MISMATCH: _fragment_cat_a_parent_page_mismatch,
    # Whole-schema page coverage invariant
    ErrorType.SCHEMA_PAGE_COVERAGE_INCOMPLETE: _fragment_schema_page_coverage_incomplete,
}


# ─── PUBLIC API ────────────────────────────────────────────────────


def build_corrective_prompt(
    base_prompt: str,
    errors: list[ValidationError],
    *,
    pdf_total_pages: int | None = None,
    max_errors_in_prompt: int = 20,
) -> str:
    """Append a corrective-instructions block to the base schema prompt.

    Parameters
    ----------
    base_prompt : str
        The unmodified base schema prompt (schema_gemini.txt or
        schema_gemini_multicolumn.txt content).
    errors : list[ValidationError]
        Errors from the previous attempt's `validate_schema()` call.
        Only ERROR-severity errors should be passed; warnings are
        surfaced separately (schema_warnings field).
    pdf_total_pages : int, optional
        Total page count, used inside fragments for clarity.
    max_errors_in_prompt : int
        Cap on how many errors to include. If the previous attempt
        produced 200 errors, we don't dump all 200 — pick the first
        20 (most representative; Gemini will fix patterns after seeing
        a handful).

    Returns the base prompt with a corrective-instructions section
    appended. The returned prompt is what the caller sends to Gemini
    for the next attempt.
    """
    if not errors:
        return base_prompt

    fragments: list[str] = []
    for err in errors[:max_errors_in_prompt]:
        builder = _FRAGMENT_BUILDERS.get(err.type)
        if builder is None:
            # Unknown error type — include a generic fragment rather than
            # silently dropping the error. This is a code error (missing
            # corrector); surfaces during dev.
            fragments.append(
                f"- Section \"{err.section_title or '?'}\" had error "
                f"{err.type.value}: {err.message}"
            )
        else:
            fragments.append(builder(err, pdf_total_pages))

    remaining = max(0, len(errors) - max_errors_in_prompt)
    truncation_note = (
        f"\n\n(Plus {remaining} more similar errors not shown — "
        f"apply the same fixes throughout.)"
        if remaining else ""
    )

    # Global contract reminder — repeated on every retry so Gemini sees
    # the most-violated rules again, not just the specific fragments. Each
    # corrective fix can otherwise introduce a NEW violation of a rule
    # Gemini "forgot" between attempts; this re-anchors all 5 core rules.
    global_reminder = (
        "\n\n"
        "## REMINDER — CRITICAL CONTRACT (must hold for EVERY section)\n\n"
        "While fixing the errors above, ALSO re-verify EVERY section in "
        "your output against these 5 rules. Do NOT introduce new "
        "violations while fixing the listed ones:\n\n"
        "1. EVERY section node has ALL these fields populated, NEVER "
        "omit any: `id`, `title`, `level`, `type`, `content_types`, "
        "`page_start`, `page_end`, `is_numbered`, "
        "`expected_question_count`, `subsections`. This applies even to "
        "bank-style sections like Classical Thinking, Critical Thinking, "
        "MCQ Bank, Practice Questions, Exercises.\n\n"
        "2. `type` is EXACTLY one of: `\"chapter\"` | `\"section\"` | "
        "`\"subsection\"`. Nothing else.\n\n"
        "3. `content_types` is a non-empty array, EXACTLY one of: "
        "`[\"theory\"]`, `[\"questions\"]`, `[\"theory\",\"figures\"]`, "
        "`[\"questions\",\"figures\"]`. NEVER `[\"theory\",\"questions\"]` "
        "(mixed). NEVER `[\"theory\",\"questions\",\"figures\"]` "
        "(mixed-with-figures). A section is EITHER theory OR questions "
        "in its OWN content_types — never both. Cat A children carry "
        "their own `[\"questions\"]` separately.\n\n"
        "4. End-of-chapter named question banks (Exercises, Practice "
        "Questions, Classical Thinking, Critical Thinking, MCQ Bank, "
        "Unit Exercise) go in `excluded_sections` (FLAT top-level "
        "array), NEVER in `sections` tree.\n\n"
        "5. `document_title` at root is the verbatim chapter/document "
        "title from the PDF, never empty.\n"
    )

    correction_block = (
        "\n\n"
        "## YOUR PREVIOUS ATTEMPT HAD ERRORS — FIX THESE\n\n"
        f"The downstream validator caught {len(errors)} error(s) in your "
        f"previous response. Re-emit the FULL schema with these specific "
        f"fixes applied:\n\n"
        + "\n\n".join(fragments)
        + truncation_note
        + global_reminder
        + "\n\n"
        "Keep all OTHER sections unchanged unless they have the same "
        "issue pattern. Output the corrected JSON only — no commentary."
    )

    return base_prompt + correction_block


__all__ = ["build_corrective_prompt"]
