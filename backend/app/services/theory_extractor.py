"""P4 Theory Extractor — per-section Gemini OCR from PDF page slices.

Each section gets its own Gemini call:
  - Slice the PDF to page_start..page_end (from schema)
  - Upload the slice to Gemini File API
  - Gemini reads the pixels and transcribes verbatim (pure OCR)
  - Returns structured JSON blocks

No LLM reasoning about content — only OCR transcription.
Retries up to MAX_ATTEMPTS times on empty/failed extraction.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from app.schemas.qc import QCResult
from app.services.invariant_splitter import paragraphs_to_blocks
from app.services.prompt_loader import load_raw
from app.services.theory_slice import SliceSpec
from app.utils.json_parse import parse_json

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
# Theory extractor model.
#
# LOCAL TEST (current): gemini-2.5-flash on the v2 extractor prompt.
# v2 prompt was designed for Flash-compatibility (explicit pattern-match
# rules, negative examples, self-check, section-boundary rule at top).
# REVERT to "gemini-2.5-pro" if quality regresses.
GEMINI_MODEL = "gemini-2.5-pro"

# Sub-retry policy for transient infra errors only (network blips, Gemini 5xx,
# read timeouts). These DO NOT count against MAX_ATTEMPTS and do not change
# anything about the extraction itself — same prompt, same slice, same model,
# same output. They only stop a transient network error from being mistaken
# for a content failure and burning a real QC attempt.
TRANSIENT_SUBSTRINGS = (
    "Server disconnected",
    "RemoteProtocolError",
    "ReadTimeout",
    "ReadError",
    "ConnectionError",
    "ConnectError",
    "ConnectTimeout",
    "503",
    "502",
    "504",
    "Connection reset",
    "Temporary failure",
)
TRANSIENT_SUB_ATTEMPTS = 4  # initial + 3 retries
TRANSIENT_BACKOFF_S = (5.0, 15.0, 45.0)

# Completeness contract — density floor used by _simple_qc to detect
# Gemini truncation. Real theory pages are typically 800-2000 chars/page;
# anything under 400 chars/page on a section that isn't legitimately short
# (Cat A parent / figure-heavy / single Points-to-Remember list) is almost
# certainly truncated and worth a retry.
MIN_CHARS_PER_PAGE = 400

_COMPLETENESS_RETRY_ADDENDUM = """
COMPLETENESS RETRY — your previous response was incomplete.
The section spans pages {ps}-{pe}. Re-scan EVERY page and emit
EVERY visible heading, paragraph, definition, list, equation,
example, and table. Do not summarize. Do not skip anything.

Target: at least 400 chars of substantive content per page.
If a page is mostly figures, emit fig captions + surrounding prose.

STOP ANCHOR STILL APPLIES — non-negotiable:
While being more thorough, you MUST still STOP at the STOP-anchor
heading specified earlier in the user prompt. "More content" means
extracting everything BEFORE the STOP heading that you missed last
time — it does NOT mean extracting content from past the STOP
heading. If the STOP heading sits on the SAME PAGE as content you
have already extracted (a same-page boundary), STOP THERE — the
content under the STOP heading belongs to the next section, not
this one. Re-read the STOP-anchor instruction above before
responding.
"""


# A chapter-level wrapper has a "bare" section_id — no dot, no hyphen,
# usually a single number ("5"), Roman numeral ("I"), or word
# ("chapter"). Subsection IDs always carry a dotted or hyphenated path
# ("5-introduction", "1.5", "1.2-illustration-1"). Used by the density
# QC carve-out so chapter wrappers — which legitimately hold minimal
# content (title + maybe 1-2 line intro) — aren't forced into the
# completeness retry, which makes Gemini overrun the STOP anchor and
# pull subsection content into the wrapper's blocks (Ch5 Geometry
# regression: §5 retry → kp blocks for Key Ideas + Remember leaked
# into the chapter wrapper, then duplicated as standalone subsections).
def _is_chapter_wrapper_id(section_id: str | None) -> bool:
    if not section_id:
        return False
    sid = section_id.strip()
    if not sid:
        return False
    # No dotted/hyphenated path = bare wrapper id
    return "-" not in sid and "." not in sid


def _is_transient(err: Exception) -> bool:
    msg = f"{type(err).__name__}: {err}"
    return any(s in msg for s in TRANSIENT_SUBSTRINGS)


async def _call_gemini_with_transient_retries(
    pdf_slice: bytes, system_prompt: str, user_prompt: str, section_id: str
) -> str:
    """Retry the SAME Gemini call on transient infra errors only.

    Non-transient errors (auth, 4xx, schema) bubble immediately so we don't
    waste time on something that can't recover. Output is identical to a
    direct call — this only changes resilience, not behaviour.
    """
    last_err: Exception | None = None
    for sub in range(TRANSIENT_SUB_ATTEMPTS):
        try:
            return await asyncio.to_thread(
                _call_gemini_ocr_sync, pdf_slice, system_prompt, user_prompt
            )
        except Exception as e:
            if not _is_transient(e):
                raise
            last_err = e
            if sub == TRANSIENT_SUB_ATTEMPTS - 1:
                break
            wait = TRANSIENT_BACKOFF_S[min(sub, len(TRANSIENT_BACKOFF_S) - 1)]
            logger.warning(
                "Gemini transient error (section=%s sub-attempt=%s/%s wait=%ss): %s",
                section_id, sub + 1, TRANSIENT_SUB_ATTEMPTS, wait, e,
            )
            await asyncio.sleep(wait)
    assert last_err is not None
    raise last_err


@dataclass
class ExtractionResult:
    section_id: str
    title: str
    blocks: list[dict]
    paragraphs: list[dict]
    qc: QCResult
    attempts: int
    local_qc_fail: bool = False
    raw_response: str = ""
    notes: str = ""
    # Unit 2 — block normalization telemetry. Populated by extract_section_with_qc
    # so QC and logs can see exactly what got coerced, dropped, etc. Plain dict
    # for JSON-friendliness (qc_local serialization).
    normalization: dict | None = None


def _slice_pdf(pdf_bytes: bytes, slice_spec: "SliceSpec") -> bytes:
    """Return a PDF containing only pages slice_spec.page_start..page_end (1-indexed).

    Unit 1 architecture: accepts a deterministic SliceSpec (page_start and
    page_end are always concrete ints, never None). NO silent fallback to
    the full PDF — if pymupdf can't produce a valid slice, raise so the
    caller marks the section status='failed' with a clear reason.
    """
    import pymupdf

    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    try:
        total = len(doc)
        # 1-indexed schema page numbers → 0-indexed pymupdf
        p0 = slice_spec.page_start - 1
        p1 = slice_spec.page_end - 1

        if p0 < 0 or p1 >= total or p0 > p1:
            raise RuntimeError(
                f"slice [{slice_spec.page_start}, {slice_spec.page_end}] "
                f"invalid for {total}-page PDF (section={slice_spec.section_id})"
            )

        out = pymupdf.open()
        try:
            out.insert_pdf(doc, from_page=p0, to_page=p1)
            return out.tobytes()
        finally:
            out.close()
    finally:
        doc.close()


def _build_user_prompt(
    section_id: str,
    title: str,
    slice_spec: "SliceSpec",
) -> str:
    """Build the user prompt for Gemini.

    Unit 1: STOP anchor uses (title, page) pair from slice_spec — Gemini
    is told to stop at the next heading on a SPECIFIC page, not just by
    name. This fixes the multi-occurrence-title bug where Gemini stopped
    at an earlier same-named heading and lost half the section.
    """
    is_container = slice_spec.is_container
    stop_title = slice_spec.stop_anchor_title
    stop_page = slice_spec.stop_anchor_page

    # Role line — PARENT vs LEAF per extractor.txt. Container sections must
    # return an empty `paragraphs` array if the first subsection heading
    # immediately follows the section heading (no intro prose in between).
    if is_container:
        boundary_phrase = (
            f"\"{title}\" and the first subsection heading \"{stop_title}\" on page {stop_page}"
            if stop_title and stop_page is not None
            else f"\"{title}\" and the first subsection heading"
        )
        role_line = (
            "This is a PARENT/CONTAINER section — it has subsections beneath it.\n"
            f"Per the PARENT vs LEAF SECTION rule in the system prompt: extract ONLY "
            f"content that appears BETWEEN this section's heading {boundary_phrase}.\n"
            "If the subsection heading immediately follows this section's heading with "
            "NO prose / equations / lists in between, return an EMPTY paragraphs array "
            "(`\"paragraphs\": []`).\n"
            "NEVER duplicate content from inside any subsection into this parent's output.\n\n"
        )
    else:
        role_line = (
            "This is a LEAF section — it has no subsections beneath it. "
            "Extract content per the standard transcription rules.\n\n"
        )

    if stop_title and stop_page is not None:
        stop_instruction = (
            f"\nSTOP extracting the MOMENT you reach the heading \"{stop_title}\" "
            f"on page {stop_page}. Anything below that heading — even a single line, "
            f"even a single equation — belongs to a different section and must NOT "
            f"appear in your output.\n"
            f"If the heading \"{stop_title}\" appears multiple times in the provided "
            f"pages (e.g. an earlier mention in a cross-reference), the AUTHORITATIVE "
            f"stop point is the heading instance on page {stop_page}. Ignore earlier "
            f"same-named occurrences — they are not the section boundary.\n"
            f"DO NOT stop earlier than \"{stop_title}\" on page {stop_page}. Continue "
            f"transcribing every paragraph, every line, every callout box, every figure "
            f"caption that appears BETWEEN \"{title}\" and that stop point. Even if the "
            f"content feels 'complete' or 'wraps up', KEEP GOING until you literally "
            f"see \"{stop_title}\" on page {stop_page}.\n"
            f"If the section's content continues onto the next page, KEEP TRANSCRIBING "
            f"on the next page until you reach the stop point. Do NOT assume a page "
            f"break means the section ended.\n"
            f"If you are uncertain whether a paragraph belongs to \"{title}\" or to the "
            f"next section, EXCLUDE it. Under-include rather than over-include — the "
            f"system can recover from a missed paragraph (retry this one section), but "
            f"it cannot recover from a section eating its neighbour's content."
        )
    else:
        stop_instruction = (
            "\nThis is the last section of its scope. Transcribe everything "
            "from the heading to the end of the provided pages."
        )
    return (
        f"Extract ALL theory content from the section titled: \"{title}\" (ID: {section_id}).\n\n"
        f"{role_line}"
        f"START extracting from the heading \"{title}\" — include everything from that heading."
        f"{stop_instruction}\n\n"
        "These PDF pages may contain content from adjacent sections. "
        "Extract ONLY the content that belongs to this section (between START heading and STOP heading).\n"
        "Transcribe EVERY word of theory content verbatim — pure OCR, no summarisation, no skipping.\n"
        "Do NOT use training knowledge. Only transcribe what you see on the pages.\n"
        "Do NOT decide the section is 'complete' on your own — completeness is determined ONLY by reaching the STOP heading.\n\n"
        f"Return JSON with section_id=\"{section_id}\" and section_title=\"{title}\"."
    )


def _call_gemini_ocr_sync(
    pdf_slice: bytes,
    system_prompt: str,
    user_prompt: str,
) -> str:
    """Upload PDF slice to Gemini and get OCR JSON back.

    Real socket timeout via ``HttpOptions`` — see app.core.gemini_runtime.
    """
    from app.core.gemini_runtime import call_gemini_with_pdf

    return call_gemini_with_pdf(
        pdf_bytes=pdf_slice,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=GEMINI_MODEL,
        max_output_tokens=32000,
        temperature=0.0,
        display_name="section.pdf",
    )


def _is_legitimately_short_section(blocks: list[dict]) -> bool:
    """Some sections are genuinely short — Points to Remember (1 list of items),
    figure-heavy galleries, Cat A parents that are mostly chips. Skip density
    check for those to avoid false retries.

    Returns True when the section's content type makes the density floor
    inappropriate.
    """
    if not blocks:
        return False
    n = len(blocks)
    chip_count = sum(
        1 for b in blocks
        if b.get("t") in ("question_ref", "exercise_ref", "example_ref")
    )
    fig_count = sum(1 for b in blocks if b.get("t") == "fig")
    list_count = sum(1 for b in blocks if b.get("t") == "list")
    # Mostly chips (Cat A parent) — short by design. Threshold tightened
    # to >=0.7 so parents that carry real body content alongside chips
    # (e.g. a Cat A parent with the SI/Dimensional-Formulae table plus
    # 2 chip refs → ratio 0.5) are NOT excused from the density check.
    # 0.5 was excusing chapters whose multi-page body tables silently
    # dropped on the floor (book Theory and Question Unit and measurement,
    # §1.5: 2 paragraphs + 2 chips over 5 pages, 76 chars/pp).
    if n >= 1 and chip_count / n >= 0.7:
        return True
    # Mostly figures
    if fig_count / n > 0.5:
        return True
    # Only a single big list block — Points to Remember pattern
    if list_count == 1 and n <= 3:
        single_list = next((b for b in blocks if b.get("t") == "list"), None)
        if single_list and len(single_list.get("items", [])) >= 3:
            return True
    return False


def _simple_qc(
    paragraphs: list[dict],
    section_id: str,
    normalization: dict | None = None,
    *,
    blocks: list[dict] | None = None,
    section_page_start: int | None = None,
    section_page_end: int | None = None,
    is_container: bool = False,
) -> QCResult:
    """Basic QC for OCR extraction.

    Unit 2 expansion: ALSO fails when block_normalizer dropped more than
    20% of incoming blocks (signal that Gemini output was malformed or
    full of unknown types — a corrective retry should help).

    Completeness contract: also fails when normalized text density falls
    below MIN_CHARS_PER_PAGE for sections that aren't legitimately short
    (Cat A parents, figure-heavy, single Points-to-Remember list).
    """
    failures = []
    if not paragraphs:
        failures.append("No blocks extracted — empty OCR result")
        return QCResult(pass_=False, score=0.0, failures=failures)

    total_words = sum(
        len((p.get("content") or p.get("prob") or "").split())
        for p in paragraphs
    )
    if total_words < 10:
        failures.append(f"Extracted content too short ({total_words} words)")

    # Unit 2 — drop-ratio check (Q3 = (b) >20% threshold).
    # Uses EFFECTIVE drop ratio: list_items consolidated into one `list`
    # block don't count as drops — they're preserved, just structurally
    # collapsed.
    if normalization is not None:
        total_in = normalization.get("total_in", 0)
        valid_out = normalization.get("valid_out", 0)
        list_items_collapsed = normalization.get("list_items_collapsed", 0)
        if total_in > 0:
            effective_out = min(total_in, valid_out + list_items_collapsed)
            dropped = total_in - effective_out
            ratio = dropped / total_in
            if ratio > 0.20:
                failures.append(
                    f"block_normalizer dropped {dropped}/{total_in} blocks "
                    f"({ratio:.0%}, threshold 20%) — Gemini output likely "
                    f"malformed; retry"
                )

        # Unit 6 — solution / question bleed threshold (>2 in one section)
        bleed = (
            normalization.get("dropped_solution_bleed", 0)
            + normalization.get("dropped_question_bleed", 0)
        )
        if bleed > 2:
            failures.append(
                f"{bleed} solution/question stem blocks leaked into theory "
                f"section — Gemini misclassifying Cat A content as theory body; "
                f"retry"
            )

    # Completeness contract — density floor. Operates on normalized blocks
    # (the canonical {t,c,...} shape) so the char counting is unambiguous.
    #
    # Density check applies to:
    #   • LEAF sections (is_container=False)
    #   • Subsection-level CONTAINERS that legitimately carry body content
    #     alongside their children (e.g. §1.5 Dimensional Formulae table
    #     in Unit & Measurement — 5 pages of table body alongside 2 chip
    #     refs; without the density check the table silently truncated).
    #
    # Density check is SKIPPED for chapter-level wrappers (is_container=
    # True AND _is_chapter_wrapper_id(section_id)). Chapter wrappers
    # genuinely have minimal content — just chapter title + optional 1-2
    # line intro before the first subsection. Forcing them to retry
    # under the completeness addendum makes Gemini extract subsection
    # content INTO the wrapper's blocks, past the STOP anchor (book Ch5
    # Geometry, §5: retry produced kp blocks for Key Ideas + Remember
    # subsections inside the chapter wrapper, then those subsections
    # also rendered standalone → duplicated content).
    if (
        blocks is not None
        and section_page_start is not None
        and section_page_end is not None
        and not (is_container and _is_chapter_wrapper_id(section_id))
    ):
        pages = max(1, section_page_end - section_page_start + 1)
        text_blocks = ("p", "kp", "def", "h1", "h2", "h3", "list")
        extracted_chars = 0
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if b.get("t") not in text_blocks:
                continue
            extracted_chars += len(b.get("c", "") or "")
            extracted_chars += len(b.get("term", "") or "")
            extracted_chars += sum(
                len(it or "") for it in (b.get("items") or [])
            )
        density = extracted_chars / pages
        if (
            not _is_legitimately_short_section(blocks)
            and density < MIN_CHARS_PER_PAGE
        ):
            failures.append(
                f"Content density too low: {int(density)} chars/page "
                f"({extracted_chars} chars over {pages} pages, threshold "
                f"{MIN_CHARS_PER_PAGE}). Section likely truncated; "
                f"retry with explicit completeness prompt."
            )

    score = 1.0 if not failures else 0.0
    return QCResult(pass_=len(failures) == 0, score=score, failures=failures)


async def extract_section_with_qc(
    section_id: str,
    title: str,
    level: int,
    pdf_bytes: bytes,
    slice_spec: "SliceSpec",
) -> ExtractionResult:
    """Extract a single section via Gemini OCR; up to MAX_ATTEMPTS retries.

    Unit 1: accepts a deterministic ``SliceSpec`` (page_start, page_end,
    is_container, and (title, page) STOP anchor) computed once by the
    theory_slice engine. No more silent page-range fallbacks.

    Container sections (``slice_spec.is_container=True``) are instructed
    to return an empty `paragraphs` array if the first subsection heading
    immediately follows the section heading with no intro prose — prevents
    duplicate-extraction of subsection content into the parent's blocks.
    """
    system_prompt = load_raw("extractor")
    user_prompt = _build_user_prompt(section_id, title, slice_spec)
    pdf_slice = _slice_pdf(pdf_bytes, slice_spec)

    last_paragraphs: list[dict] = []
    last_raw = ""
    last_failures: list[str] = []

    from app.services.block_normalizer import normalize_blocks

    def _norm_dict(nresult) -> dict:
        """Flatten NormalizationResult to a JSON-safe dict for ExtractionResult."""
        return {
            "total_in": nresult.total_in,
            "valid_out": nresult.valid_out,
            "type_coerced": nresult.type_coerced,
            "empty_dropped": nresult.empty_dropped,
            "malformed_dropped": nresult.malformed_dropped,
            "dropped_solution_bleed": nresult.dropped_solution_bleed,
            "dropped_question_bleed": nresult.dropped_question_bleed,
            "list_items_collapsed": nresult.list_items_collapsed,
            "unknowns_seen": dict(nresult.unknowns_seen),
            "drop_details": list(nresult.drop_details[:20]),  # cap for storage
        }

    last_normalization: dict | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            # Completeness-retry addendum — if a prior attempt flagged
            # truncation, append a stronger instruction to this attempt's
            # prompt. Doesn't change retry count; only the prompt for the
            # current attempt.
            effective_user_prompt = user_prompt
            if attempt > 1 and any(
                ("Content density" in f) or ("truncated" in f)
                for f in last_failures
            ):
                effective_user_prompt = user_prompt + _COMPLETENESS_RETRY_ADDENDUM.format(
                    ps=slice_spec.page_start, pe=slice_spec.page_end
                )

            raw = await _call_gemini_with_transient_retries(
                pdf_slice, system_prompt, effective_user_prompt, section_id
            )
            data = parse_json(raw)
            paragraphs = list(data.get("paragraphs") or [])
            notes = data.get("notes", "") or ""

            # Unit 2 — normalize before QC so drop-ratio is visible to QC.
            nresult = normalize_blocks(
                paragraphs, section_title=title, section_id=section_id
            )
            ndict = _norm_dict(nresult)
            if nresult.type_coerced or nresult.empty_dropped or nresult.malformed_dropped:
                logger.info(
                    "block_normalizer section=%s %s",
                    section_id, nresult.summary(),
                )

            qc = _simple_qc(
                paragraphs,
                section_id,
                normalization=ndict,
                blocks=nresult.blocks,
                section_page_start=slice_spec.page_start,
                section_page_end=slice_spec.page_end,
                is_container=slice_spec.is_container,
            )

            last_paragraphs = paragraphs
            last_raw = raw
            last_normalization = ndict
            last_failures = list(qc.failures)

            if qc.pass_:
                return ExtractionResult(
                    section_id=section_id,
                    title=title,
                    blocks=nresult.blocks,
                    paragraphs=paragraphs,
                    qc=qc,
                    attempts=attempt,
                    local_qc_fail=False,
                    raw_response=raw,
                    notes=notes,
                    normalization=ndict,
                )

            logger.info("OCR QC failed (section=%s attempt=%s): %s", section_id, attempt, qc.failures)

        except Exception as e:
            logger.warning("OCR extraction failed (section=%s attempt=%s): %s", section_id, attempt, e)
            if attempt == MAX_ATTEMPTS:
                failed_qc = QCResult(pass_=False, score=0.0, failures=[f"OCR error: {e}"])
                return ExtractionResult(
                    section_id=section_id,
                    title=title,
                    blocks=[],
                    paragraphs=[],
                    qc=failed_qc,
                    attempts=attempt,
                    local_qc_fail=True,
                    normalization=last_normalization,
                )

    # All attempts exhausted — re-normalize the last paragraphs and persist.
    nresult = normalize_blocks(
        last_paragraphs, section_title=title, section_id=section_id
    )
    last_normalization = _norm_dict(nresult)
    qc = _simple_qc(
        last_paragraphs,
        section_id,
        normalization=last_normalization,
        blocks=nresult.blocks,
        section_page_start=slice_spec.page_start,
        section_page_end=slice_spec.page_end,
        is_container=slice_spec.is_container,
    )
    return ExtractionResult(
        section_id=section_id,
        title=title,
        blocks=nresult.blocks,
        paragraphs=last_paragraphs,
        qc=qc,
        attempts=MAX_ATTEMPTS,
        local_qc_fail=True,
        raw_response=last_raw,
        normalization=last_normalization,
    )


async def re_extract_with_fix(
    section_id: str,
    title: str,
    level: int,
    pdf_bytes: bytes,
    slice_spec: "SliceSpec",
) -> ExtractionResult:
    """User-triggered re-extraction — same as extract_section_with_qc, fresh attempt.

    Unit 1: accepts the same deterministic SliceSpec as extract_section_with_qc.
    """
    return await extract_section_with_qc(
        section_id=section_id,
        title=title,
        level=level,
        pdf_bytes=pdf_bytes,
        slice_spec=slice_spec,
    )
