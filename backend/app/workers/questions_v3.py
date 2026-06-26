"""Stage 3 question extraction worker — section-aligned (mirrors theory).

This worker replaces the v2 excluded-block-driven approach with a clean
section-aligned pattern that mirrors how theory extraction already works:

  for each section in the approved schema:
      slice PDF to section's pages
      ask Gemini to extract questions for THIS section, stop at next heading
      structurally filter the LLM output
      persist Question rows tagged to that section
      compare extracted count vs schema's expected_question_count

Why this design solves the question-bank pain points:

  • Missing/incomplete  → schema's `expected_question_count` is the target
                          and "extracted N of M" is now reportable.
  • Cross-section dups  → impossible by construction; each Q belongs to one
                          section because we slice one section at a time.
  • Hallucinated/wrong  → tighter prompt (v3) + post-extraction structural
                          filter rejects items that aren't questions.
  • "What's happening?" → per-section status (complete/partial/failed) +
                          per-section retry surface for the UI.

The v2 task is left in place so existing banks keep working; new banks default
to v3 (controlled by a feature flag in a follow-up step).
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.heartbeat import Heartbeat
from app.core.storage import download_pdf
from app.models.book import Book
from app.models.job import Job
from app.models.question import Question
from app.models.question_bank import QuestionBank
from app.schemas.analyser import BookSchema, ExcludedSection, SchemaSection
from app.services.prompt_loader import load_raw
from app.services.question_latex_normalizer import (
    QuestionLatexReport,
    normalize_question_latex,
)
from app.services.questions.dedup import dedup_bank
from app.services.questions.structural_filter import filter_items
from app.utils.json_parse import parse_json
from app.workers.celery_app import celery_app
from app.workers.runner import register as register_task

logger = logging.getLogger(__name__)

_sync_engine = create_engine(settings.SYNC_DATABASE_URL, pool_pre_ping=True)
SyncSession = sessionmaker(bind=_sync_engine, class_=Session, autoflush=False)

GEMINI_MODEL = "gemini-2.5-flash"  # OCR-style transcription; Flash is ~4× cheaper than Pro at parity
MAX_ATTEMPTS = 3
GEMINI_TIMEOUT_S = 150
MAX_OUTPUT_TOKENS = 65536

# Q6 Part A — Question Completeness Contract.
# A unit is "incomplete" when it captured fewer than COMPLETENESS_THRESHOLD of
# its schema-declared expected_question_count. Incomplete units get a bounded
# number of full re-scan retries (separate budget from the existing Q3 targeted
# retry and Q1/Q2 post-passes) before we accept the gap and record it.
#
# Threshold tightened 0.9 → 1.0 (Tier A): the 10% slack was letting marginal
# misses through. Observed on Indefinite Integrals Sample / Critical Thinking:
# extracted 24 of 26 expected = 92.3% → considered "complete" at 0.9 →
# Pass-3 page-by-page recovery never fired → Q9 and Q23 silently dropped.
# At 1.0, any gap (even 1 of 100) triggers Pass-3 → page-by-page recovery
# → missing q-numbers are surfaced to Gemini with an explicit skip-list of
# what's already extracted. Cost is bounded by existing gates
# (MAX_UNITS_PER_BOOK=20, MAX_PAGES_PER_UNIT=12).
COMPLETENESS_THRESHOLD = 1.0
MAX_COMPLETENESS_RETRIES = 2

# Sub-retry policy for transient OCR errors — Gemini server disconnects, read
# timeouts, 5xx. Doesn't burn through MAX_ATTEMPTS, just waits for the network
# blip to resolve. Same prompt, same slice — output unchanged.
_TRANSIENT_SUBSTRINGS = (
    "Server disconnected", "RemoteProtocolError", "ReadTimeout", "ReadError",
    "ConnectionError", "ConnectError", "ConnectTimeout",
    "503", "502", "504", "Connection reset", "Temporary failure",
)
_TRANSIENT_SUB_ATTEMPTS = 4
_TRANSIENT_BACKOFF_S = (5.0, 15.0, 45.0)


def _is_transient(err: Exception) -> bool:
    msg = f"{type(err).__name__}: {err}"
    return any(s in msg for s in _TRANSIENT_SUBSTRINGS)


async def _gemini_call_with_transient_retries(
    pdf_slice: bytes, system_prompt: str, user_prompt: str, ctx: str
) -> str:
    last_err: Exception | None = None
    for sub in range(_TRANSIENT_SUB_ATTEMPTS):
        try:
            return await asyncio.to_thread(
                _call_gemini_sync, pdf_slice, system_prompt, user_prompt
            )
        except Exception as e:
            if not _is_transient(e):
                raise
            last_err = e
            if sub == _TRANSIENT_SUB_ATTEMPTS - 1:
                break
            wait = _TRANSIENT_BACKOFF_S[min(sub, len(_TRANSIENT_BACKOFF_S) - 1)]
            logger.warning(
                "Gemini transient error (%s sub-attempt=%s/%s wait=%ss): %s",
                ctx, sub + 1, _TRANSIENT_SUB_ATTEMPTS, wait, e,
            )
            await asyncio.sleep(wait)
    assert last_err is not None
    raise last_err


# ---------------------------------------------------------------------------
# Job + bank update helpers (heartbeat-aware)
# ---------------------------------------------------------------------------
def _update_job(session: Session, job_id: UUID, **fields: Any) -> None:
    job = session.get(Job, job_id)
    if job is None:
        return
    for k, v in fields.items():
        setattr(job, k, v)
    job.last_heartbeat_at = datetime.now(timezone.utc)
    session.commit()


def _update_bank(session: Session, bank_id: UUID, **fields: Any) -> None:
    bank = session.get(QuestionBank, bank_id)
    if bank is None:
        return
    for k, v in fields.items():
        setattr(bank, k, v)
    session.commit()


# ---------------------------------------------------------------------------
# PDF slicing — same as theory_extractor's helper, copied here to keep this
# worker self-contained.
# ---------------------------------------------------------------------------
def _slice_pdf(pdf_bytes: bytes, page_start: int | None, page_end: int | None) -> bytes:
    try:
        import pymupdf

        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        total = len(doc)
        p0 = max(0, (page_start or 1) - 1)
        p1 = min(total - 1, (page_end or total) - 1)
        if p0 > p1 or p0 >= total:
            doc.close()
            return pdf_bytes
        out = pymupdf.open()
        out.insert_pdf(doc, from_page=p0, to_page=p1)
        result = out.tobytes()
        out.close()
        doc.close()
        return result
    except Exception as e:
        logger.warning("PDF slice failed (pages %s-%s): %s — using full PDF",
                       page_start, page_end, e)
        return pdf_bytes


def _effective_slice_end(
    page_end: int | None,
    next_page_start: int | None,
) -> int | None:
    """Q6 Part C — compute the padded slice end without bleeding into the
    next section.

    We add the historical +1 trailing pad (a section's last question often
    sits on the page the next section "officially" starts). But when the
    next section starts on or before that padded page, we CLIP the pad to
    the next section's start page so the slice still includes the shared
    boundary page (needed for this section's tail) WITHOUT extending a full
    page deeper into the next section's territory.

    The prompt's STOP-anchor (next_title) still does the fine-grained
    heading-level cut on the shared page; this clip is the coarse page-level
    guard that keeps the slice from ever reaching pages that belong wholly
    to the section AFTER the next one.

      page_end=None                       → None   (caller passes full PDF)
      next_page_start=None                → page_end + 1  (last unit; full pad)
      page_end=3, next_page_start=3       → 3      (clip pad to boundary page)
      page_end=3, next_page_start=5       → 4      (full +1 pad; no bleed)
      page_end=3, next_page_start=4       → 4      (+1 pad lands on next start)
    """
    if page_end is None:
        return None
    padded = page_end + 1
    if next_page_start is None:
        return padded
    # Never extend past the next section's start page. The boundary page
    # itself (== next_page_start) is allowed so this section's tail on the
    # shared page is still captured; the prompt STOP-anchor trims the rest.
    return min(padded, next_page_start)


def _extract_section_text(
    pdf_bytes: bytes,
    page_start: int | None,
    page_end: int | None,
) -> str:
    """Extract pypdf text for a section's page range as one string.

    Used by Q3.5 verify_extraction (substring check) and Q3.6 deterministic
    question detector. Returns "" on extraction failure (e.g. scanned PDF
    where pypdf cannot read text). Callers must be defensive — empty string
    means "no text-based verification possible; rely on Gemini Vision only".
    """
    if page_start is None or page_end is None:
        return ""
    try:
        from pypdf import PdfReader
    except Exception as e:
        logger.warning("pypdf not available — section text slice empty: %s", e)
        return ""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        total = len(reader.pages)
        p0 = max(0, page_start - 1)
        p1 = min(total - 1, page_end - 1)
        if p0 > p1 or p0 >= total:
            return ""
        parts: list[str] = []
        for p in range(p0, p1 + 1):
            try:
                parts.append(reader.pages[p].extract_text() or "")
            except Exception as e:
                logger.debug("text extract failed page %s: %s", p + 1, e)
        return "\n".join(parts)
    except Exception as e:
        logger.warning(
            "section text extraction failed (pages %s-%s): %s",
            page_start, page_end, e,
        )
        return ""


# ---------------------------------------------------------------------------
# Q3.5 — verify_extraction()
# Structural verifier that runs each extracted question against the source
# text slice (from _extract_section_text). Detects fabrication and degrades
# suspect fields rather than discarding the whole question, EXCEPT when
# raw_text itself fails — then the question is rejected.
#
# Architecture rule 3:
#   - Substring check: ≥90% of question's >3-char tokens must appear in
#     source slice (lowered to 70% if LaTeX/math markers present).
#   - Options: must find ≥2 option markers in source slice within ~200 chars
#     of question text, else strip options field.
#   - Answer/solution: must find "Answer"/"Ans"/"Solution"/"Sol"/"=" near
#     question, else strip answer field.
#   - q_no: must literally appear in source slice, else null it.
#   - Failures degrade (strip suspect field), don't discard, unless raw_text
#     itself fails — then reject and log.
#
# This helper is STANDALONE — it does NOT mutate the input dict. The caller
# (Q3) wires it into the extraction flow, applies the degrade actions, and
# rejects questions where verified=False AND reason mentions raw_text.
# ---------------------------------------------------------------------------
_MATH_MARKER_RE = re.compile(r"\\frac|\\sqrt|\\times|\\div|\\sum|\\int|\^|_\{|=")
_OPTION_MARKER_RE = re.compile(
    r"(?:^|\s)(?:\(?[A-Da-d]\)|\([ivxIVX]+\)|[1-9]\d?\.|\([1-9]\d?\))(?=[\s)])"
)
_ANSWER_NEAR_RE = re.compile(
    r"(?i)\b(?:answer|ans|solution|sol|hence|therefore|=)\b"
)


@dataclass
class VerificationResult:
    """Outcome of verify_extraction() for one question.

    Attributes:
        verified:        Whether the question's raw_text passed substring check.
                         If False, the caller should REJECT this question
                         entirely (it is likely fabricated).
        stripped_fields: List of optional fields whose verification failed.
                         The caller should null/remove these from the question
                         before persisting (degrade rather than discard).
                         Possible values: "options", "answer", "solution", "q_no".
        reason:          Human-readable explanation of failures (for logging).
        skipped:         True if no source text slice was available
                         (scanned PDF). In this case verified=True but no
                         text-based checks ran — caller should rely on
                         Gemini Vision OCR alone.
    """
    verified: bool
    stripped_fields: list[str] = field(default_factory=list)
    reason: str | None = None
    skipped: bool = False


def _tokens_for_match(s: str) -> list[str]:
    """Return >3-char alphanumeric tokens from a string for substring matching.

    Lower-cased, strips LaTeX backslash commands (which often vary by source
    rendering). ALSO strips {{fig: ...}} placeholders entirely — those are
    not in the source PDF text and would falsely deflate the coverage
    ratio. Observed: figure-heavy chemistry MCQs (ACFROG) had 47/47
    questions rejected because {{fig: (i) — (unlabelled diagram)}}
    placeholders dominated raw_text tokens, dragging coverage to 40%.
    Used for the raw_text substring overlap check.
    """
    if not s:
        return []
    # Strip {{fig: ...}} placeholders so they don't count against coverage.
    # These are extractor-emitted markers indicating an inline figure
    # location; they are NOT in the source PDF text and rejecting on
    # them is a false positive.
    no_fig = re.sub(r"\{\{\s*fig\s*:.*?\}\}", " ", s, flags=re.IGNORECASE | re.DOTALL)
    # Strip LaTeX commands like \frac, \sqrt — they're often present in
    # extracted JSON but rendered differently in pypdf text.
    cleaned = re.sub(r"\\[a-zA-Z]+", " ", no_fig)
    # Pull alphanumeric tokens longer than 3 chars.
    toks = re.findall(r"[A-Za-z0-9]{4,}", cleaned)
    return [t.lower() for t in toks]


def _has_math_markers(s: str) -> bool:
    """Detect LaTeX/math markers in a string. Triggers the lowered (70%)
    substring threshold because pypdf often mangles math rendering."""
    if not s:
        return False
    return bool(_MATH_MARKER_RE.search(s))


def verify_extraction(
    question: dict[str, Any],
    source_text: str,
) -> VerificationResult:
    """Run structural checks on one extracted question against the section's
    source text slice.

    See module-level Q3.5 docstring for full rules. Returns a
    VerificationResult; does NOT mutate the input question dict.

    Caller should:
      - If result.skipped: persist the question as-is (no text-based verify
        possible; e.g. scanned PDF with no pypdf text).
      - If result.verified is False: REJECT the question (likely fabrication).
      - If result.verified is True and result.stripped_fields is non-empty:
        DEGRADE — null/remove those fields from the question before persisting.
    """
    # Skip path: no source text slice available (scanned PDF). Cannot
    # text-verify — pass through and let Gemini Vision be the source of truth.
    if not source_text or not source_text.strip():
        return VerificationResult(verified=True, skipped=True,
                                  reason="no source text slice available")

    raw_text = (question.get("raw_text") or "").strip()
    if not raw_text:
        return VerificationResult(
            verified=False,
            reason="raw_text empty — cannot verify; rejecting",
        )

    src_lower = source_text.lower()
    stripped: list[str] = []
    reasons: list[str] = []

    # 1) raw_text substring check — what fraction of >3-char tokens appear in source?
    qtokens = _tokens_for_match(raw_text)
    if not qtokens:
        # Question is too short / pure math symbols. Treat as skipped rather
        # than reject; can't reliably verify either way.
        return VerificationResult(verified=True, skipped=True,
                                  reason="question too short for token match")

    matched = sum(1 for t in qtokens if t in src_lower)
    coverage = matched / len(qtokens)
    threshold = 0.70 if _has_math_markers(raw_text) else 0.90
    if coverage < threshold:
        return VerificationResult(
            verified=False,
            reason=(f"raw_text token coverage {coverage:.0%} < threshold "
                    f"{threshold:.0%} ({matched}/{len(qtokens)} tokens "
                    f"found in source)"),
        )

    # raw_text passed. Now run optional-field degradation checks.

    # 2) options check — must find ≥2 option markers near question text.
    options = question.get("options")
    if isinstance(options, list) and len(options) >= 2:
        # Find the question's anchor in the source (use first 5 tokens as
        # signature; widen to 200 chars on each side).
        anchor_toks = qtokens[:5]
        anchor_idx = -1
        if anchor_toks:
            for tok in anchor_toks:
                idx = src_lower.find(tok)
                if idx >= 0:
                    anchor_idx = idx
                    break
        if anchor_idx >= 0:
            window_start = max(0, anchor_idx - 50)
            window_end = min(len(src_lower), anchor_idx + 200)
            window = source_text[window_start:window_end]
            marker_count = len(_OPTION_MARKER_RE.findall(window))
            if marker_count < 2:
                stripped.append("options")
                reasons.append(
                    f"options stripped: only {marker_count} option markers "
                    f"in 200-char window around question (need ≥2)"
                )
        else:
            stripped.append("options")
            reasons.append("options stripped: question anchor not found in source")

    # 3) answer / solution check — must find an answer keyword near question.
    has_answer_field = any(
        question.get(f) for f in ("answer", "solution", "ans")
    )
    if has_answer_field:
        anchor_toks = qtokens[:5]
        anchor_idx = -1
        if anchor_toks:
            for tok in anchor_toks:
                idx = src_lower.find(tok)
                if idx >= 0:
                    anchor_idx = idx
                    break
        if anchor_idx >= 0:
            window_start = max(0, anchor_idx - 50)
            window_end = min(len(src_lower), anchor_idx + 400)
            window = source_text[window_start:window_end]
            if not _ANSWER_NEAR_RE.search(window):
                for f in ("answer", "solution", "ans"):
                    if question.get(f):
                        stripped.append(f)
                reasons.append(
                    "answer/solution stripped: no answer keyword near "
                    "question in source"
                )
        else:
            for f in ("answer", "solution", "ans"):
                if question.get(f):
                    stripped.append(f)
            reasons.append(
                "answer/solution stripped: question anchor not found in source"
            )

    # 4) q_no check — must literally appear in source.
    q_no = question.get("q_no")
    if q_no is not None and q_no != "":
        q_no_str = str(q_no).strip()
        # Match q_no with optional trailing dot/paren (e.g. "5", "5.", "(5)").
        # Search whole source — q_no can appear anywhere on the page.
        if q_no_str and q_no_str.lower() not in src_lower:
            stripped.append("q_no")
            reasons.append(f"q_no stripped: '{q_no_str}' not literally in source")

    return VerificationResult(
        verified=True,
        stripped_fields=stripped,
        reason="; ".join(reasons) if reasons else None,
    )


# ---------------------------------------------------------------------------
# Q3.6 — deterministic_question_detector()
# Regex-based pre-pass that scans the source text slice for printed question
# markers and returns the authoritative expected_count + candidate_qnos.
#
# Architecture rule 4:
#   expected_count comes from a deterministic regex detector run on the source
#   slice BEFORE Gemini, NOT from schema's expected_question_count. Patterns:
#     - line-start "\d+\." (numbered list)
#     - "Q\.?\s*\d+" (Q.5, Q5)
#     - "Example \d+" / "Problem \d+" / "Exercise \d+"
#
# Q3 uses the (count, candidate_qnos) tuple to:
#   1. Decide if the section needs a retry: if extracted < count, run targeted retry.
#   2. Tell the targeted retry which q_nos are missing so Gemini focuses on them.
#
# Sub-parts (a)(b)(c) under one parent number are NOT counted separately —
# this matches the extractor.txt rule. Roman numerals (i)(ii)(iii) likewise.
#
# Returns (count, qnos). count == len(qnos). qnos are deduplicated and
# sorted in source-order. Empty source returns (0, []).
# ---------------------------------------------------------------------------

# Patterns recognised as a TOP-LEVEL question marker. Each pattern captures
# a normalised q_no string. ORDER MATTERS — most specific first so e.g.
# "Example 9.1" matches before bare "9.1" picks it up.
_DETECTOR_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # "Example 9.1", "EXAMPLE 9.1", "Worked Example 5", "Solved Example 2.3"
    ("example",
     re.compile(r"(?im)^\s*(?:WORKED\s+|SOLVED\s+)?EXAMPLE\s+(\d+(?:\.\d+)?)\b")),
    # "Problem 5", "Practice Problem 4", "PROBLEM 7"
    ("problem",
     re.compile(r"(?im)^\s*(?:PRACTICE\s+)?PROBLEM\s+(\d+(?:\.\d+)?)\b")),
    # "Exercise 8.3 Q.4" — top-level Exercise treated separately from inner items.
    # If we see "Exercise N.M" header alone (no Q after) that's the section start;
    # the inner questions are matched by Q.\d or \d+\. patterns below.
    # "Q.5" / "Q5" / "Q. 5"
    ("q_no",
     re.compile(r"(?im)^\s*Q\.?\s*(\d+(?:\.\d+)?)\b")),
    # "Question 5"
    ("q_no",
     re.compile(r"(?im)^\s*Question\s+(\d+(?:\.\d+)?)\b")),
    # Numbered list at line start: "1.", "12.", "  3."  (not "1.5" — that has a
    # second dot; we want a SINGLE-trailing dot, no decimals after).
    ("numbered",
     re.compile(r"(?m)^\s*(\d{1,3})\.(?!\d)")),
]


def deterministic_question_detector(source_text: str) -> tuple[int, list[str]]:
    """Scan ``source_text`` for printed question markers and return
    ``(count, candidate_qnos)``.

    See module-level Q3.6 docstring for full rules. Empty input returns
    ``(0, [])``. Sub-parts (a)(b)(c) and roman (i)(ii)(iii) are NOT counted.

    The candidate_qnos list is deduplicated and ordered by first appearance
    in the source. Each q_no is a normalised string (the captured number,
    not the full marker — so "Example 9.1" yields "9.1", "Q.5" yields "5",
    numbered list "12." yields "12").

    Q3 will pass these q_nos to a targeted retry prompt to focus Gemini on
    missing items. The full marker (e.g. "Example 9.1") is reconstructable
    from context — the retry prompt sees the same source text.
    """
    if not source_text or not source_text.strip():
        return (0, [])

    seen: dict[str, int] = {}  # q_no → first-seen char offset (for ordering)
    for kind, pat in _DETECTOR_PATTERNS:
        for m in pat.finditer(source_text):
            q_no = m.group(1).strip()
            if q_no and q_no not in seen:
                seen[q_no] = m.start()

    # Order by first appearance in source.
    ordered = sorted(seen.items(), key=lambda kv: kv[1])
    qnos = [q for q, _ in ordered]
    return (len(qnos), qnos)


# ---------------------------------------------------------------------------
# Section iteration — flatten the schema into the units we extract from.
# Includes: every regular section, every excluded section. Excluded sections
# (chapter-end exercise blocks) are by far the densest source of questions.
# ---------------------------------------------------------------------------
class _Unit:
    """One extraction unit: a section or excluded section with page range."""

    __slots__ = ("kind", "id", "title", "page_start", "page_end",
                 "expected", "next_title", "next_page_start", "skipped",
                 "section_start_heading")

    def __init__(
        self,
        kind: str,                    # "section" | "excluded"
        ref_id: str,
        title: str,
        page_start: int | None,
        page_end: int | None,
        expected: int | None,
        skipped: bool = False,
        section_start_heading: str | None = None,
    ) -> None:
        self.kind = kind
        self.id = ref_id
        self.title = title
        self.page_start = page_start
        self.page_end = page_end
        self.expected = expected
        self.next_title: str | None = None
        # Q6 Part C — page_start of the NEXT unit in schema order. Used by
        # _effective_slice_end to clip this unit's trailing page pad so it
        # never bleeds into the next section's questions. Set in the same
        # wiring loop as next_title. None for the last unit (no successor).
        self.next_page_start: int | None = None
        # When True, the unit is recorded in extraction_stats with
        # status="skipped" but no Gemini call is made. Used for the
        # "trust the schema's eqc=0" cost optimisation.
        self.skipped = skipped
        # Multi-column books only — verbatim printed heading text that
        # marks where THIS section begins on its first page. Lets the
        # extractor skip the tail of the previous section that shares
        # the boundary page (e.g. Critical Thinking 4.1 starts mid-
        # page 434 right after Classical Thinking 4.3 ends). None for
        # single-column books so their prompt text is byte-identical
        # to before this change.
        self.section_start_heading = section_start_heading


def _flatten_sections(
    schema: BookSchema,
    *,
    is_multi_column: bool = False,
) -> list[_Unit]:
    """Walk the schema depth-first and emit one extraction unit per node.

    Parameters
    ----------
    is_multi_column: when True, each unit carries the section's printed
        heading as ``section_start_heading``. The extractor uses that
        to skip the tail of the previous section on the shared boundary
        page (e.g. Critical Thinking 4.1 starts mid-page 434 after
        Classical Thinking 4.3 ends; without the heading hint Gemini
        captures the wrong questions). Single-column books pass False
        so prompt text stays byte-identical to before this change.

    Coverage policy (changed 2026-05-05):
      - Every schema node (section AND subsection) becomes its own unit, so
        worked-example subsections like "EXAMPLE 9.1" get a focused Gemini
        call instead of being buried inside a parent theory section.
      - A parent is skipped IF its children's page ranges fully cover the
        parent's range — otherwise we'd slice the same pages twice. If the
        parent has any "residual" pages not covered by children (theory pages
        sitting next to example subsections), the parent IS emitted so those
        pages still get scanned.
      - Leaf nodes always emit (their pages aren't covered by anyone else).
      - Chapter wrappers ("9 Modern Physics" spanning the whole book) are
        skipped — only their children are real extraction units.
      - Excluded sections (chapter-end exercise blocks) appended after.

    Why this matters: in the previous policy, "X-rays" (p4-6, mostly theory)
    was sliced as one unit and Gemini missed EXAMPLE 9.1 sitting on p6. With
    per-node units, EXAMPLE 9.1 gets its own narrow call ("transcribe the
    question on p6 titled EXAMPLE 9.1") and can't be missed.
    """
    units: list[_Unit] = []

    def emit(node: SchemaSection, depth: int) -> None:
        # Chapter wrapper handling:
        # - Normal case: chapter is a container with children — recurse
        #   only, never emit chapter itself (would re-cover the whole
        #   book).
        # - Special case (pure question-bank PDFs): chapter has
        #   content_types=["questions"] AND zero subsections. Gemini
        #   correctly classified the whole PDF as one Cat A block but
        #   there are no sub-headings to nest. If we recurse-only here,
        #   ZERO units get emitted → ZERO Gemini calls → ZERO questions
        #   extracted. Treat the chapter itself as the Cat A leaf and
        #   fall through to the normal emit path.
        if (node.type or "").lower() == "chapter":
            # Wrapper rule: chapter is emitted as a Cat A unit IFF its
            # content_types includes "questions" (i.e. loose questions
            # sit directly under the chapter heading with no enclosing
            # subsection — §10.4). Otherwise the chapter is a pure
            # container; recurse only.
            has_q_at_chapter = "questions" in (node.content_types or [])
            if not has_q_at_chapter:
                for c in node.subsections or []:
                    emit(c, depth + 1)
                return
            # Fall through to the normal Cat A emit path below. The
            # P-1 HARD STOP further down explicitly skips chapter
            # wrappers so the wrapper's loose Qs aren't blocked by
            # the presence of Cat A children.
        # Excluded sections inside .sections tree (rare) handled below
        if (node.type or "").lower() == "excluded":
            return

        children = node.subsections or []

        # Decide whether to emit THIS node as its own Cat A unit. Suppress it
        # when its children already OWN its content — either because:
        #   (a) the children's page ranges fully cover the parent's range, OR
        #   (b) the parent has Cat A (question) children.
        # The schema may over-report parent.page_end vs the leaves' actual
        # coverage (observed Indefinite Integrals book: parent
        # 4-critical-thinking claims pages 434-435 but its only leaf claims
        # 434 only → 26 questions on page 435 would attach to the parent
        # instead of the leaf), so the Cat-A-children check (b) suppresses the
        # parent even when (a) leaves residual pages. Either way the children
        # are the real extraction units; the parent is just a container.
        #
        # ONE exception — a chapter WRAPPER carrying its own loose §10.4
        # questions directly under the chapter heading. Those have no
        # enclosing subsection, so the children NEVER cover them, and the
        # wrapper's expQ counts ONLY those loose questions (it does not roll
        # up children) — so it must still be emitted. Previously this was two
        # separate suppressors: a page-coverage check with NO wrapper
        # exemption (which ran first) + this Cat-A-children check WITH one —
        # so a wrapper sharing its page with a Cat A child (e.g. a one-page
        # book) was wrongly suppressed and its loose questions silently
        # dropped. Unified here so the exemption applies to BOTH conditions.
        # (If real prelude theory exists before the first child's heading,
        # the theory pipeline still extracts it via the next_title hard-stop,
        # extract.py:411-416.)
        is_chapter_wrapper = (node.type or "").lower() == "chapter"
        has_cat_a_children = any(
            "questions" in (c.content_types or [])
            for c in children
        )
        pages_fully_covered = False
        if children and node.page_start is not None and node.page_end is not None:
            covered: set[int] = set()
            for c in children:
                if c.page_start is not None and c.page_end is not None:
                    covered.update(range(c.page_start, c.page_end + 1))
            parent_pages = set(range(node.page_start, node.page_end + 1))
            pages_fully_covered = not (parent_pages - covered)

        emit_self = True
        if (has_cat_a_children or pages_fully_covered) and not is_chapter_wrapper:
            emit_self = False

        if emit_self:
            # CATEGORY A FILTER (Q1) — only sections explicitly tagged as
            # questions in the schema get a Gemini call. Category B (theory
            # aids: Illustration, Activity, Progress Check, Try It, etc.)
            # and pure-theory sections are skipped because their
            # content_types is ["theory"]. This is the architectural
            # guarantee that questions cannot land in non-question sections.
            #
            # Excluded sections (chapter-end exercise banks) are appended
            # below via a separate code path and are NOT subject to this
            # filter — they are always extracted with verbatim printed
            # titles per Q5.
            is_category_a = "questions" in (node.content_types or [])
            if is_category_a:
                eqc = node.expected_question_count
                units.append(_Unit(
                    kind="section",
                    ref_id=node.id,
                    title=node.title,
                    page_start=node.page_start,
                    page_end=node.page_end,
                    expected=eqc,
                    skipped=False,
                    section_start_heading=node.title if is_multi_column else None,
                ))

        for c in children:
            emit(c, depth + 1)

    for s in schema.sections:
        emit(s, 0)

    # ARCHITECTURE RULE (Q5 — locked):
    #   Excluded section titles are passed VERBATIM from the schema into
    #   _Unit.id and _Unit.title. NO normalization, NO reclassification, NO
    #   keyword cleanup. The user has explicitly required: "extract them
    #   also, add in same folders and exact same names — don't classify
    #   based on your knowledge or create or recreate, use pure ocr".
    #   Examples that must round-trip unchanged:
    #     "Exercise 1.6 Multiple choice questions"
    #     "Unit Exercise - 1"
    #     "PRACTICE QUESTIONS"
    #     "Crossword"
    #   Do NOT introduce title-normalization here. If the printed wording
    #   varies between books (e.g. some books use "Practice Problems",
    #   others "Practice Questions"), preserve whichever the schema
    #   captured — the schema is the source of truth, the book is too.
    for ex in schema.excluded_sections or []:
        ec = ex.expected_question_count
        # Always extract excluded blocks unless the schema explicitly said 0.
        if ec == 0 and not (ex.subsections or []):
            continue

        children = ex.subsections or []
        if children:
            # Mirror the PDF: emit each printed sub-heading
            # ("Very Short Answer", "MCQs", "Numerical Problems", …) as its
            # own extraction unit. The parent block is NOT emitted because
            # its pages are fully covered by its children — extracting both
            # would produce duplicates that cross-section dedup catches but
            # wastes Gemini calls.
            for c in children:
                cec = c.expected_question_count
                if cec == 0:
                    continue
                units.append(_Unit(
                    kind="excluded",
                    # ref_id format: "<parent_title>::<child_title>" (verbatim)
                    ref_id=f"{ex.title}::{c.title}" if c.title else (ex.title or ""),
                    title=c.title or ex.title or "",
                    page_start=c.page_start or ex.page_start,
                    page_end=c.page_end or ex.page_end,
                    expected=cec,
                    section_start_heading=(c.title or ex.title) if is_multi_column else None,
                ))
        else:
            units.append(_Unit(
                kind="excluded",
                ref_id=ex.title or "",   # verbatim from schema
                title=ex.title or "",    # verbatim from schema
                page_start=ex.page_start,
                page_end=ex.page_end,
                expected=ec,
                section_start_heading=(ex.title or None) if is_multi_column else None,
            ))

    # Preserve schema declaration order — regular Cat A sections first
    # (depth-first walk), then excluded sections in schema-declaration order.
    # An earlier page-range sort here was a latent bug: when excluded
    # sections share or overlap page ranges, the stable sort produced
    # unexpected reordering that diverged from `flatten_sections(schema)`.
    # Downstream consumers (final_merge, extraction_stats, logs) assume
    # schema order — keep it.

    for i, u in enumerate(units):
        if i + 1 < len(units):
            u.next_title = units[i + 1].title
            u.next_page_start = units[i + 1].page_start
        else:
            u.next_title = None
            u.next_page_start = None

    # Page-coverage audit — warn if any pages between first and last unit
    # are NOT covered by any unit. Helps detect schema gaps that would
    # silently miss questions.
    if units:
        covered: set[int] = set()
        for u in units:
            ps, pe = u.page_start, u.page_end
            if ps is not None and pe is not None:
                for p in range(ps, pe + 1):
                    covered.add(p)
        first = min((u.page_start for u in units if u.page_start is not None), default=None)
        last = max((u.page_end for u in units if u.page_end is not None), default=None)
        if first is not None and last is not None:
            missing = sorted(set(range(first, last + 1)) - covered)
            if missing:
                logger.warning(
                    "schema page-coverage gap detected — pages NOT covered "
                    "by any extraction unit: %s. These pages will be SKIPPED. "
                    "Consider widening adjacent section page ranges in the schema.",
                    missing,
                )

    n_section = sum(1 for u in units if u.kind == "section")
    n_excluded = sum(1 for u in units if u.kind == "excluded")
    logger.info(
        "questions_v3: %s extraction units (%s Category A sections + %s excluded blocks). "
        "Theory and theory-aid sections skipped per Category A filter (Q1).",
        len(units), n_section, n_excluded,
    )

    return units


# ---------------------------------------------------------------------------
# Gemini call (sync, runs inside asyncio.to_thread for the heartbeat to tick)
# ---------------------------------------------------------------------------
def _build_user_prompt(unit: _Unit) -> str:
    stop = (
        f"\nSTOP extracting when you reach the heading \"{unit.next_title}\" — "
        "do NOT include any content from that heading onwards."
        if unit.next_title
        else ""
    )
    # Multi-column boundary hint — only present when the book was uploaded
    # with the multi-column flag (set in _flatten_sections by the worker
    # entry point). Tells Gemini the verbatim printed heading where THIS
    # section begins so it can skip the previous section's tail on a
    # shared boundary page. Single-column books pass None here and this
    # branch contributes nothing — prompt text stays byte-identical to
    # pre-fix behaviour.
    start_anchor = ""
    if unit.section_start_heading:
        start_anchor = (
            f"\nBOUNDARY: This section starts on its first page AFTER the "
            f"printed heading \"{unit.section_start_heading}\". "
            f"Any numbered items printed on that page BEFORE that heading "
            f"belong to the previous section — do NOT include them, even "
            f"if their question numbers look like \"1.\", \"2.\", etc. "
            f"Start counting / extracting only from the first item printed "
            f"AFTER the section's start heading."
        )
    return (
        f"Transcribe verbatim every question-like item that is visibly printed "
        f"in the section titled: \"{unit.title}\" (ID: {unit.id}).\n"
        f"START at the heading \"{unit.title}\".{start_anchor}{stop}\n\n"
        "These PDF pages may contain content from adjacent sections. "
        "Extract ONLY items that belong to this section.\n\n"
        "If this section is pure theory and contains NO question-like items "
        "(no numbered exercises, no MCQs, no labelled worked examples, no "
        "'Try It' / 'Check Your Understanding' prompts), return "
        "identified_total: 0 and extracted: []. That is a correct and complete "
        "answer for a theory-only section — do NOT invent items to fill the "
        "list.\n\n"
        "Pure OCR only. Never use training knowledge. Never compute, complete, "
        "or paraphrase a solution. Never invent MCQ options. Never insert a "
        "figure placeholder for a figure not visibly on the page.\n\n"
        f"Return JSON with section_id=\"{unit.id}\" and "
        f"section_title=\"{unit.title}\"."
    )


def _call_gemini_sync(pdf_slice: bytes, system_prompt: str, user_prompt: str) -> str:
    from app.core.gemini_runtime import call_gemini_with_pdf

    return call_gemini_with_pdf(
        pdf_bytes=pdf_slice,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=GEMINI_MODEL,
        timeout_s=GEMINI_TIMEOUT_S,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        temperature=0.0,
        display_name="section.pdf",
    )


# ---------------------------------------------------------------------------
# Per-unit extraction
# ---------------------------------------------------------------------------
async def _extract_unit(
    unit: _Unit,
    pdf_bytes: bytes,
    system_prompt: str,
) -> dict[str, Any]:
    """Returns a per-unit result dict suitable for persisting + reporting."""
    # Page-padding rules:
    # • Trailing pad +1 — sections often place their last question on the
    #   page where the next section "officially" starts.
    # • Leading pad −1 — example units are typically marked at a single page
    #   (page_start == page_end) but the example body can begin on the
    #   previous page (e.g. label "EXAMPLE 9.1" wraps in from prior page).
    #   For these tight 1-page slices we widen by one page on each side so
    #   Gemini reliably finds the example.
    # The user prompt instructs the model to START at the named heading and
    # STOP at the next, so over-wide pads stay safe — extra pages are ignored
    # if they don't contain the section's content.
    is_example_unit = bool(unit.id) and "example" in (unit.id or "").lower()
    is_tight_slice = (
        unit.page_start is not None
        and unit.page_end is not None
        and unit.page_end - unit.page_start <= 0
    )
    leading_pad = 1 if (is_example_unit and is_tight_slice and unit.page_start and unit.page_start > 1) else 0
    start = (unit.page_start - leading_pad) if unit.page_start is not None else None
    padded_end = _effective_slice_end(unit.page_end, unit.next_page_start)
    pdf_slice = _slice_pdf(pdf_bytes, start, padded_end)
    user_prompt = _build_user_prompt(unit)

    last_err = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = await _gemini_call_with_transient_retries(
                pdf_slice, system_prompt, user_prompt,
                ctx=f"q-extract {unit.kind}/{unit.id}",
            )
            data = parse_json(raw)
            if not isinstance(data, dict):
                raise ValueError("response was not a JSON object")

            identified = int(data.get("identified_total") or 0)
            extracted = list(data.get("extracted") or [])

            # Structural filter — drop anything that isn't actually a question.
            fr = filter_items(extracted)
            kept = fr.kept
            rejected = fr.rejected

            return {
                "ok": True,
                "attempts": attempt,
                "identified_total": identified,
                "extracted": kept,
                "rejected": rejected,
                "rejected_count": len(rejected),
                "raw_response_size": len(raw or ""),
            }
        except Exception as e:
            last_err = f"attempt {attempt}: {e}"
            logger.warning("v3 extract failed (%s/%s): %s",
                           unit.kind, unit.id, last_err)

    return {
        "ok": False,
        "attempts": MAX_ATTEMPTS,
        "identified_total": 0,
        "extracted": [],
        "rejected": [],
        "rejected_count": 0,
        "error": last_err,
    }


# ---------------------------------------------------------------------------
# Chunked extraction for large units
# ---------------------------------------------------------------------------
# Threshold above which a unit gets split into smaller page-range chunks.
# Single Gemini calls handle ~25–40 questions reliably; beyond that, the JSON
# output gets long and error-prone. PRACTICE blocks can have 70+ questions.
_CHUNK_THRESHOLD = 30
_CHUNK_PAGE_SPAN = 4  # pages per chunk when splitting a large unit


def _normalize_for_strict_match(s: str) -> str:
    """Aggressive whitespace + punctuation collapse for 100% dedup match.

    Treats `"What is x?"` and `" what  is  x ? "` as equal, but keeps
    different LaTeX expressions and different numbers distinct. We only
    drop *cosmetic* differences (whitespace, case, trailing punctuation),
    not content.
    """
    import re as _re
    s = (s or "").lower()
    s = _re.sub(r"\s+", " ", s).strip()
    # Drop trailing/leading punctuation noise only (preserve internal)
    s = s.strip(" .,:;!?“”‘’\"'")
    return s


_OPTION_LETTER_RE = re.compile(r"\(([A-Da-d1-4])\)")


def _quality_check_question(item: dict[str, Any]) -> list[str]:
    """Multi-column safety net — detect common column-wrap extraction
    failures and return a list of warning strings.

    Designed to be a NO-OP for clean single-column extractions: braces
    always balance, options always appear in order. Only flags real
    structural issues. Used downstream to set `qc_status='warn'` on
    suspicious items so the UI can surface them for review.

    Checks:
      - Brace balance: count of `{` must equal count of `}`
      - Option order: (A)(B)(C)(D) must appear in alphabetical order
      - Missing options: if any of A..D is present, the full set A..N
        must be present (no gaps)

    None of these checks are fatal — they only annotate. The question
    is still emitted; reviewers in the UI can decide what to do.
    """
    raw = (item.get("raw_text") or "").strip()
    if not raw:
        return []

    warnings: list[str] = []

    # 1. Brace balance
    open_braces = raw.count("{")
    close_braces = raw.count("}")
    if open_braces != close_braces:
        warnings.append(
            f"unbalanced_braces({open_braces}_open_vs_{close_braces}_close)"
        )

    # 2. Option order + missing options
    letters = _OPTION_LETTER_RE.findall(raw)
    if letters:
        upper = [l.upper() for l in letters]
        # Limit to A-D for ordering check (some books use 1-4 too;
        # those typically aren't column-wrap-broken so skip the strict
        # check for digit options).
        ad_only = [l for l in upper if l in "ABCD"]
        if ad_only and ad_only != sorted(ad_only):
            warnings.append(f"options_out_of_order:{''.join(ad_only)}")
        if ad_only:
            expected = list("ABCD"[: len(ad_only)])
            missing = sorted(set(expected) - set(ad_only))
            if missing:
                warnings.append(f"missing_options:{''.join(missing)}")

    return warnings


def _dedupe_extracted(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-section dedup — number-aware (Q6 Part D).

    Two extracted items are duplicates ONLY when they refer to the SAME
    printed question. We decide that as follows:

      • If BOTH items carry a question_number → they are duplicates iff
        their normalized question_number is EQUAL. Two questions with
        DIFFERENT question_numbers are NEVER duplicates, even when their
        raw_text is byte-identical (short MCQs that legitimately share
        wording — "Choose the correct option." stems — must not collapse).
      • If a question_number is ABSENT → fall back to the historical
        strict normalized-raw_text dedup (handles chunk-boundary repeats
        of unnumbered items).

    This stops the previous text-only dedup from silently eating
    legitimately-distinct short questions that happen to share wording.

    Used after chunk-merge so the same question on a page-range boundary
    that Gemini returned twice doesn't produce two DB rows.
    """
    seen_numbers: set[str] = set()   # normalized question_number
    seen_text: set[str] = set()      # normalized raw_text (numberless fallback)
    out: list[dict[str, Any]] = []
    for it in items:
        qno = _norm_qno(
            it.get("question_number") or it.get("q_no") or ""
        )
        if qno:
            # Number-keyed dedup. Different numbers are always distinct.
            if qno in seen_numbers:
                continue  # true duplicate (same printed number), drop silently
            seen_numbers.add(qno)
        else:
            # Numberless fallback — strict normalized-text dedup.
            key = _normalize_for_strict_match(it.get("raw_text") or "")
            if not key:
                out.append(it)  # empty raw_text — keep it, let QC catch later
                continue
            if key in seen_text:
                continue  # exact duplicate, drop silently
            seen_text.add(key)
        # Multi-column safety net — annotate any structural issues we
        # can detect from raw_text alone (brace balance, option order,
        # missing options). NO-OP for clean single-column extractions.
        # Attaches a `qc_warnings: list[str]` field when issues found.
        # Downstream persistence reads this for the question's qc_status.
        warns = _quality_check_question(it)
        if warns:
            it = {**it, "qc_warnings": warns}
        out.append(it)
    return out


async def _extract_unit_maybe_chunked(
    unit: _Unit,
    pdf_bytes: bytes,
    system_prompt: str,
) -> dict[str, Any]:
    """Run extraction. For large units, split by page range and merge.

    Defence-in-depth: even though the JSON parser now tolerates malformed
    LaTeX escapes, very long Gemini responses (75+ questions) are still the
    riskiest call shape. Chunking by page range keeps each call's output
    small and bounded, then merges + dedupes.
    """
    expected = unit.expected or 0
    span = (
        (unit.page_end - unit.page_start + 1)
        if unit.page_start is not None and unit.page_end is not None
        else 0
    )

    if expected <= _CHUNK_THRESHOLD or span <= _CHUNK_PAGE_SPAN:
        return await _extract_unit(unit, pdf_bytes, system_prompt)

    # Split page range into windows of _CHUNK_PAGE_SPAN pages each.
    chunks: list[tuple[int, int]] = []
    p = unit.page_start
    assert unit.page_end is not None
    while p <= unit.page_end:
        chunks.append((p, min(p + _CHUNK_PAGE_SPAN - 1, unit.page_end)))
        p += _CHUNK_PAGE_SPAN

    logger.info(
        "v3 chunking %s (%s) — expected=%s, span=%s pages → %s chunk(s)",
        unit.id, unit.title, expected, span, len(chunks),
    )

    # Run chunks in parallel; each gets the SAME unit metadata but a
    # narrowed page range. We mutate a shallow copy.
    async def _run_chunk(ps: int, pe: int) -> dict[str, Any]:
        from copy import copy as _copy
        sub = _copy(unit)
        sub.page_start = ps
        sub.page_end = pe
        return await _extract_unit(sub, pdf_bytes, system_prompt)

    results = await asyncio.gather(
        *[_run_chunk(ps, pe) for ps, pe in chunks],
        return_exceptions=False,
    )

    merged_extracted: list[dict[str, Any]] = []
    merged_rejected: list[dict[str, Any]] = []
    identified_sum = 0
    any_ok = False
    last_err = ""
    total_attempts = 0
    for r in results:
        identified_sum += int(r.get("identified_total") or 0)
        merged_extracted.extend(r.get("extracted") or [])
        merged_rejected.extend(r.get("rejected") or [])
        total_attempts += int(r.get("attempts") or 0)
        if r.get("ok"):
            any_ok = True
        elif r.get("error"):
            last_err = r["error"]

    deduped = _dedupe_extracted(merged_extracted)
    return {
        "ok": any_ok,
        "attempts": total_attempts,
        "identified_total": identified_sum,
        "extracted": deduped,
        "rejected": merged_rejected,
        "rejected_count": len(merged_rejected),
        "error": last_err if not any_ok else "",
        "_chunked": True,
        "_chunks": len(chunks),
    }


# ---------------------------------------------------------------------------
# Q3 — verify + targeted retry wrapper
# Wraps _extract_unit_maybe_chunked with two additional passes:
#   1. Verification — every extracted item runs through verify_extraction()
#      against the section's pypdf text slice. Failures REJECT the question
#      (likely fabrication); per-field failures DEGRADE the question (strip
#      suspect field but keep raw_text).
#   2. Targeted retry — for excluded sections only, if verified count is
#      below the deterministic detector count, build a retry prompt that
#      names the missing q_nos and runs ONE more Gemini call. Verify retry
#      results, append unique ones, then accept.
#
# Architecture rules 3 + 4 + 5 enforced here.
# Cat A "section" units are NOT subject to detector-based retry — the
# detector overcounts when multiple Cat A sections share a page (e.g.
# Examples 1.1, 1.2, 1.3 on page 4). For Cat A units, verification still
# runs (rule 3) but expected count stays as schema_eqc.
# ---------------------------------------------------------------------------
async def _run_targeted_retry(
    unit: _Unit,
    pdf_bytes: bytes,
    system_prompt: str,
    missing_qnos: list[str],
) -> dict[str, Any]:
    """Run one targeted retry call for the unit, naming the missing q_nos.

    Single Gemini call, no chunking, max 1 retry per section. Caller is
    responsible for verifying the retry results before accepting.
    """
    is_example = bool(unit.id) and "example" in (unit.id or "").lower()
    is_tight = (
        unit.page_start is not None
        and unit.page_end is not None
        and unit.page_end - unit.page_start <= 0
    )
    leading_pad = 1 if (
        is_example and is_tight and unit.page_start and unit.page_start > 1
    ) else 0
    start = (unit.page_start - leading_pad) if unit.page_start is not None else None
    padded_end = _effective_slice_end(unit.page_end, unit.next_page_start)
    pdf_slice = _slice_pdf(pdf_bytes, start, padded_end)

    qnos_str = ", ".join(missing_qnos[:50])  # cap to avoid prompt bloat
    user_prompt = (
        f"TARGETED RETRY pass for section: \"{unit.title}\" (ID: {unit.id}).\n\n"
        f"On a previous pass the following question numbers were detected on "
        f"the source pages but NOT extracted: {qnos_str}.\n\n"
        f"Re-scan the pages and extract ONLY those numbered items. Apply the "
        f"same OCR-only rules — verbatim transcription, no fabrication, no "
        f"invented options/answers. If you cannot find a listed q_no on the "
        f"page, OMIT it (do not invent a question to fill the slot).\n\n"
        f"Return JSON with section_id=\"{unit.id}\", section_title=\"{unit.title}\", "
        f"identified_total = number of items you found from the missing list, "
        f"and `extracted` containing only the missing items."
    )
    try:
        raw = await _gemini_call_with_transient_retries(
            pdf_slice, system_prompt, user_prompt,
            ctx=f"q-retry {unit.kind}/{unit.id}",
        )
        data = parse_json(raw)
        if not isinstance(data, dict):
            return {"ok": False, "extracted": [], "rejected": [], "identified_total": 0}
        extracted = list(data.get("extracted") or [])
        fr = filter_items(extracted)
        return {
            "ok": True,
            "identified_total": int(data.get("identified_total") or 0),
            "extracted": fr.kept,
            "rejected": fr.rejected,
        }
    except Exception as e:
        logger.warning("Q3 targeted retry failed for %s/%s: %s",
                       unit.kind, unit.id, e)
        return {"ok": False, "extracted": [], "rejected": [], "identified_total": 0}


def _is_incomplete(expected: int | None, extracted: int) -> bool:
    """Q6 Part A — pure completeness decision.

    A unit is incomplete when it has a positive expected count AND captured
    fewer than COMPLETENESS_THRESHOLD of it. expected<=0 means "no
    expectation" → never incomplete. extracted==expected (or above) →
    complete. At exactly the threshold (e.g. 9 of 10) → complete.
    """
    exp = int(expected or 0)
    if exp <= 0:
        return False
    return extracted < exp * COMPLETENESS_THRESHOLD


def _qno_of(item: dict[str, Any]) -> str:
    """Normalized question_number for merge keys (empty if absent)."""
    for k in ("question_number", "q_no"):
        v = item.get(k)
        if v is not None and str(v).strip():
            return _norm_qno(v)
    return ""


def _merge_by_number(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Q6 Part A — merge newly-found questions into the existing set by
    question_number (NOT aggressive text-dedup). Returns (merged, added).

    Rules:
      • An incoming item whose normalized question_number already exists is
        skipped (already captured).
      • An incoming item with a NEW question_number is appended.
      • An incoming item WITHOUT a question_number is appended only if its
        normalized raw_text isn't already present (numberless fallback) —
        mirrors _dedupe_extracted so retries can't manufacture duplicates.
    """
    seen_numbers = {_qno_of(it) for it in existing if _qno_of(it)}
    seen_text = {
        _normalize_for_strict_match(it.get("raw_text") or "")
        for it in existing
        if not _qno_of(it) and (it.get("raw_text") or "").strip()
    }
    merged = list(existing)
    added = 0
    for it in incoming:
        qno = _qno_of(it)
        if qno:
            if qno in seen_numbers:
                continue
            seen_numbers.add(qno)
            merged.append(it)
            added += 1
        else:
            key = _normalize_for_strict_match(it.get("raw_text") or "")
            if not key or key in seen_text:
                continue
            seen_text.add(key)
            merged.append(it)
            added += 1
    return merged, added


async def _run_completeness_retry(
    unit: _Unit,
    pdf_bytes: bytes,
    system_prompt: str,
    expected: int,
    extracted: int,
) -> dict[str, Any]:
    """Q6 Part A — one full re-scan of the unit's ENTIRE page range with a
    completeness addendum. Unlike the Q3 targeted retry (which names specific
    missing q_nos), this re-extracts the whole section because the gap may be
    a wholesale column-miss (e.g. the entire right column was skipped).

    Returns the same shape as _extract_unit (ok/extracted/rejected/...).
    """
    is_example = bool(unit.id) and "example" in (unit.id or "").lower()
    is_tight = (
        unit.page_start is not None
        and unit.page_end is not None
        and unit.page_end - unit.page_start <= 0
    )
    leading_pad = 1 if (
        is_example and is_tight and unit.page_start and unit.page_start > 1
    ) else 0
    start = (unit.page_start - leading_pad) if unit.page_start is not None else None
    padded_end = _effective_slice_end(unit.page_end, unit.next_page_start)
    pdf_slice = _slice_pdf(pdf_bytes, start, padded_end)

    base_prompt = _build_user_prompt(unit)
    addendum = (
        f"\n\n═══ COMPLETENESS RETRY ═══\n"
        f"This section is expected to contain {expected} questions "
        f"(numbered 1..{expected} or similar). The previous pass found only "
        f"{extracted}. Re-scan the ENTIRE page range. Extract EVERY numbered "
        f"question. Read multi-column layouts column-by-column, left-to-right, "
        f"reading each column fully top-to-bottom before moving to the next "
        f"(see the MULTI-COLUMN rule). Do NOT skip questions, do NOT merge two "
        f"questions into one item, do NOT drop questions that contain figures "
        f"or tables. Return the COMPLETE set. (Still obey RULE B — never "
        f"fabricate; if a number genuinely isn't printed, omit it.)"
    )
    user_prompt = base_prompt + addendum
    try:
        raw = await _gemini_call_with_transient_retries(
            pdf_slice, system_prompt, user_prompt,
            ctx=f"q6-completeness {unit.kind}/{unit.id}",
        )
        data = parse_json(raw)
        if not isinstance(data, dict):
            return {"ok": False, "extracted": [], "rejected": [], "identified_total": 0}
        extracted_items = list(data.get("extracted") or [])
        fr = filter_items(extracted_items)
        return {
            "ok": True,
            "identified_total": int(data.get("identified_total") or 0),
            "extracted": fr.kept,
            "rejected": fr.rejected,
        }
    except Exception as e:
        logger.warning("Q6 completeness retry failed for %s/%s: %s",
                       unit.kind, unit.id, e)
        return {"ok": False, "extracted": [], "rejected": [], "identified_total": 0}


def _verify_and_degrade(
    items: list[dict[str, Any]],
    source_text: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Run verify_extraction on each item.

    Returns (verified_items, rejected_items, degraded_count).
      - verified_items: items that passed (with stripped fields nulled)
                        PLUS items that failed the raw_text substring check
                        but have real content — these are KEPT and flagged
                        low-confidence / needs-review (Q6 Part D).
      - rejected_items: ONLY items that are empty / garbage (no usable
                        raw_text). Strict-substring failure no longer drops
                        a real question — it merely flags it.
      - degraded_count: how many kept items had at least one stripped field

    Q6 Part D rationale: the strict ≥90% substring check drops valid
    questions whenever pypdf text drifts from Gemini's OCR (figure-bearing
    MCQs, scanned columns, math-heavy stems). Silently losing a real
    question is worse than surfacing a low-confidence one. We therefore
    only DROP when raw_text is genuinely empty; coverage failures KEEP the
    question and mark qc_local.needs_review so a human can confirm.
    """
    verified: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    degraded = 0
    for item in items:
        vr = verify_extraction(item, source_text)
        if vr.skipped:
            verified.append(item)
            continue
        if not vr.verified:
            reason = vr.reason or "verify failed"
            raw_text = (item.get("raw_text") or "").strip()
            # Only TRUE garbage (empty raw_text) is dropped. A coverage
            # failure on a non-empty question is kept + flagged.
            is_empty_garbage = (
                not raw_text or "raw_text empty" in reason
            )
            if is_empty_garbage:
                rejected.append({
                    **item,
                    "_q3_reject_reason": reason,
                })
                continue
            # Keep but flag low-confidence / needs-review.
            flagged = {
                **item,
                "_q3_low_confidence": True,
                "_q3_verify_reason": reason,
            }
            verified.append(flagged)
            continue
        if vr.stripped_fields:
            cleaned = {**item}
            for f in vr.stripped_fields:
                if f in cleaned:
                    cleaned[f] = None
            cleaned["_q3_stripped"] = vr.stripped_fields
            verified.append(cleaned)
            degraded += 1
        else:
            verified.append(item)
    return verified, rejected, degraded


async def _extract_unit_with_verify_and_retry(
    unit: _Unit,
    pdf_bytes: bytes,
    system_prompt: str,
) -> dict[str, Any]:
    """Q3: extract + verify + targeted retry once, single entry point.

    Replaces direct _extract_unit_maybe_chunked calls in the per-unit
    pipeline. Behaviour:
      1. Initial extraction (delegates to _extract_unit_maybe_chunked).
      2. Verification of every extracted item against the pypdf text slice.
      3. For excluded sections only: detector pre-count + targeted retry of
         missing q_nos. Max 1 retry per section.
      4. Final result has the same shape as upstream + new fields:
         verified_count, degraded_count, detector_count,
         authoritative_expected, _q3_retried, _q3_status.
    """
    # 1. Initial extraction.
    result = await _extract_unit_maybe_chunked(unit, pdf_bytes, system_prompt)
    if not result.get("ok"):
        # Failed call — pass through; caller will mark as failed.
        result["_q3_status"] = "failed"
        return result

    # 2. Pull source text slice (using ORIGINAL page range, not padded —
    # we want clean per-section text for verify/detect).
    source_text = _extract_section_text(pdf_bytes, unit.page_start, unit.page_end)

    # 3. Verify every extracted item.
    initial_extracted = list(result.get("extracted") or [])
    verified_items, rejected_by_verify, degraded = _verify_and_degrade(
        initial_extracted, source_text,
    )
    if rejected_by_verify:
        logger.warning(
            "Q3 verify rejected %d/%d items in %s/%s — sample reasons: %s",
            len(rejected_by_verify), len(initial_extracted),
            unit.kind, unit.id,
            [r.get("_q3_reject_reason", "")[:80] for r in rejected_by_verify[:3]],
        )

    # 4. Compute authoritative expected count.
    # Cat A "section" units: schema_eqc — detector overcounts on shared pages.
    # Excluded sections (Q-banks): three-way priority:
    #   (1) detector_count (regex on pypdf text)            — most authoritative
    #   (2) Gemini's identified_total when self-consistent  — fallback for scanned PDFs
    #   (3) schema_eqc (analyse-pass estimate)              — last resort, can be wrong
    #
    # Why fallback to identified_total: on scanned PDFs pypdf returns no
    # text → detector=0. Schema's eqc is also a Gemini guess and can over-
    # or under-count (e.g. Chapter 5 COMPETITION WING: schema said 107, real
    # is 61, Gemini-extract identified=61). Using identified_total when
    # extracted matches it gives an internally-consistent count and
    # prevents false "partial" markings.
    detector_count = 0
    detector_qnos: list[str] = []
    if source_text:
        detector_count, detector_qnos = deterministic_question_detector(source_text)

    initial_verified_count = len(verified_items)
    identified_total = int(result.get("identified_total") or 0)

    if unit.kind == "excluded":
        if detector_count > 0:
            # pypdf could read; detector wins
            authoritative_expected = detector_count
        elif (
            identified_total > 0
            and abs(initial_verified_count - identified_total) <= 2
        ):
            # Scanned section — trust Gemini's self-consistent count
            authoritative_expected = initial_verified_count
        else:
            # Last resort — schema's eqc (may be wrong)
            authoritative_expected = unit.expected or 0
        # Mutate unit.expected so downstream stats / classify use the
        # corrected number.
        unit.expected = authoritative_expected
    else:
        authoritative_expected = unit.expected or 0

    # 5. Targeted retry decision.
    verified_count = len(verified_items)
    needs_retry = (
        unit.kind == "excluded"        # only retry chapter-end Q-banks
        and authoritative_expected > 0
        and verified_count < authoritative_expected
        and bool(source_text)          # need text to compute missing q_nos
        and detector_count > 0
    )
    retried = False
    if needs_retry:
        # Compute which q_nos were extracted vs which detector found.
        extracted_qnos = set()
        for item in verified_items:
            for k in ("question_number", "q_no"):
                v = (item.get(k) or "").strip()
                if v:
                    extracted_qnos.add(v)
                    break
        missing_qnos = [q for q in detector_qnos if q not in extracted_qnos]
        if missing_qnos:
            logger.info(
                "Q3 targeted retry: %s/%s — verified=%d expected=%d "
                "detector=%d missing=%s",
                unit.kind, unit.id, verified_count, authoritative_expected,
                detector_count, missing_qnos[:10],
            )
            retry_result = await _run_targeted_retry(
                unit, pdf_bytes, system_prompt, missing_qnos,
            )
            retried = True
            retry_extracted = list(retry_result.get("extracted") or [])
            retry_verified, retry_rejected, retry_degraded = _verify_and_degrade(
                retry_extracted, source_text,
            )
            # Append uniquely (avoid double-counting on overlap with first pass).
            for item in retry_verified:
                qno = ""
                for k in ("question_number", "q_no"):
                    v = (item.get(k) or "").strip()
                    if v:
                        qno = v
                        break
                if qno and qno in extracted_qnos:
                    continue
                if qno:
                    extracted_qnos.add(qno)
                verified_items.append(item)
                if "_q3_stripped" in item:
                    degraded += 1
            rejected_by_verify.extend(retry_rejected)
            verified_count = len(verified_items)

    # 5b. Q6 Part A — COMPLETENESS CONTRACT.
    # After verify + targeted retry, compare the captured count against the
    # schema's expected_question_count. If we're below the threshold, run up
    # to MAX_COMPLETENESS_RETRIES full re-scans of the ENTIRE page range,
    # merging newly-found questions by question_number (never aggressive
    # text-dedup). Separate budget from the Q3 targeted retry above.
    #
    # Applies to BOTH Cat A sections and excluded Q-banks: a wholesale
    # column-miss (the entire right column skipped) is the dominant cause of
    # the measured 90.3% capture rate, and the targeted retry above (excluded-
    # only, q_no-named) can't recover a column that was never read.
    completeness_incomplete = False
    completeness_gap = 0
    completeness_retries_used = 0
    expected_for_gate = int(unit.expected or 0)
    if expected_for_gate > 0 and _is_incomplete(expected_for_gate, verified_count):
        completeness_incomplete = True
        for attempt in range(1, MAX_COMPLETENESS_RETRIES + 1):
            if not _is_incomplete(expected_for_gate, verified_count):
                break
            logger.info(
                "Q6 completeness retry %d/%d: %s/%s — captured=%d expected=%d "
                "(threshold=%.0f%%)",
                attempt, MAX_COMPLETENESS_RETRIES, unit.kind, unit.id,
                verified_count, expected_for_gate, COMPLETENESS_THRESHOLD * 100,
            )
            completeness_retries_used = attempt
            cr = await _run_completeness_retry(
                unit, pdf_bytes, system_prompt,
                expected=expected_for_gate, extracted=verified_count,
            )
            if not cr.get("ok"):
                break
            cr_extracted = list(cr.get("extracted") or [])
            cr_verified, cr_rejected, cr_degraded = _verify_and_degrade(
                cr_extracted, source_text,
            )
            verified_items, added = _merge_by_number(verified_items, cr_verified)
            degraded += cr_degraded
            rejected_by_verify.extend(cr_rejected)
            verified_count = len(verified_items)
            if added == 0:
                # Retry found nothing new — further retries won't help.
                break
        completeness_gap = max(0, expected_for_gate - verified_count)
        logger.info(
            "Q6 completeness gate result: %s/%s — final captured=%d expected=%d "
            "gap=%d retries=%d still_incomplete=%s",
            unit.kind, unit.id, verified_count, expected_for_gate,
            completeness_gap, completeness_retries_used,
            _is_incomplete(expected_for_gate, verified_count),
        )

    # 6. Decide _q3_status (independent of downstream _classify_unit).
    if verified_count == 0:
        q3_status = "empty" if authoritative_expected == 0 else "partial"
    elif authoritative_expected and verified_count < authoritative_expected:
        q3_status = "partial"
    else:
        q3_status = "complete"

    # 7. Patch result. Existing consumers see the same shape; we add a few
    # diagnostic fields. result["extracted"] is now the verified list.
    merged_rejected = list(result.get("rejected") or []) + rejected_by_verify
    result["extracted"] = verified_items
    result["rejected"] = merged_rejected
    result["rejected_count"] = len(merged_rejected)
    result["verified_count"] = verified_count
    result["degraded_count"] = degraded
    result["detector_count"] = detector_count
    result["authoritative_expected"] = authoritative_expected
    result["_q3_retried"] = retried
    result["_q3_status"] = q3_status
    # Q6 Part A telemetry.
    result["completeness_incomplete"] = completeness_incomplete
    result["completeness_gap"] = completeness_gap
    result["completeness_retries"] = completeness_retries_used
    return result


# ---------------------------------------------------------------------------
# Q1 — Solution completeness
# ---------------------------------------------------------------------------
def _finalize_solution_flag(
    solution_text: str | None,
) -> tuple[str | None, bool]:
    """Single source of truth for the solution data-integrity invariant.

    ``has_solution`` MUST equal ``(solution_text is non-empty after strip)``.
    Never flag=True with empty text; never text with flag=False.

    Runs AFTER ``normalize_question_latex`` at every write site (Q5 normalize
    first, then finalize). Returns ``(clean_text, has_solution_bool)``:
      - empty / None / whitespace-only → ``(None, False)``
      - otherwise                      → ``(stripped_text, True)``
    """
    if not solution_text:
        return None, False
    cleaned = solution_text.strip()
    if not cleaned:
        return None, False
    return cleaned, True


# Word-boundary patterns for sections whose SOURCE TYPE guarantees a printed
# solution (worked examples / solved problems). Case-insensitive. We only
# require a solution where the type guarantees one — never fabricate.
_SOLUTION_BEARING_RE = re.compile(
    r"\b(?:worked\s+example|solved\s+example|example|solved|illustration)\b",
    re.IGNORECASE,
)


def _section_implies_solution(
    section_title: str | None,
    section_ref: str | None,
    kind: str | None,
) -> bool:
    """Conditional guard: does this section's source type guarantee that
    every item prints a complete solution?

    True for worked-example / solved-problem / illustration sections —
    detected via ``kind == 'example'`` OR a word-boundary match in the
    section title / ref. False for plain MCQ banks and exercises (which
    routinely print NO solution), so we never flag those as incomplete.
    """
    if (kind or "").strip().lower() == "example":
        return True
    for field in (section_title, section_ref):
        if field and _SOLUTION_BEARING_RE.search(field):
            return True
    return False


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _normalize_question_fields(
    raw_text: str, solution_text: str | None
) -> tuple[str, str | None, QuestionLatexReport]:
    """Run the KaTeX normalizer over a question's raw_text + solution_text
    (options live inline in raw_text — no separate column). Returns the cleaned
    pair plus a combined telemetry report for per-unit aggregation."""
    raw_out, rep = normalize_question_latex(raw_text or "")
    sol_out = solution_text
    if solution_text:
        sol_out, rep_sol = normalize_question_latex(solution_text)
        rep = rep + rep_sol
    return raw_out, sol_out, rep


def _persist_unit(
    session: Session,
    bank_id: UUID,
    book_id: UUID,
    unit: _Unit,
    result: dict[str, Any],
) -> int:
    """Replace existing rows for this (bank, section_ref) with the new ones.

    Returns the number of Question rows inserted.

    ARCHITECTURE RULE (Q4 — locked):
      `section_ref` and `section_title` for every persisted Question and
      RejectedQuestion row MUST be stamped from `unit.id` / `unit.title`
      respectively. NEVER from Gemini's output. The model is never trusted
      to label its own section. Item-level fields like `question_number`
      and `kind` are model-supplied and may differ per row, but the
      section anchor is owned by the worker.

      Do NOT add `section_ref=item.get("section_ref")` or any equivalent
      anywhere in this function. If you need to track Gemini's claimed
      label for diagnostics, store it under a different field name
      (e.g. `_model_claimed_section`).
    """
    from app.models.rejected_question import RejectedQuestion
    from app.services.section_identity import resolve_section_uuid

    # Phase 2 of canonical identity migration (CONTRACT.md §1):
    # resolve unit.id (slug) to Section UUID once, then stamp every
    # Question we insert with the FK. Falls back to None if no Section
    # row matches — backfill / Phase 3 reader handles that case.
    section_uuid = resolve_section_uuid(session, book_id, unit.id)

    session.execute(
        delete(Question).where(
            Question.bank_id == bank_id,
            Question.section_ref == unit.id,
        )
    )
    # Wipe pending rejects from a previous run for this section so the user
    # sees a fresh review queue. Restored/discarded rows from earlier runs
    # are preserved (audit trail).
    session.execute(
        delete(RejectedQuestion).where(
            RejectedQuestion.bank_id == bank_id,
            RejectedQuestion.section_ref == unit.id,
            RejectedQuestion.status == "pending",
        )
    )

    # Persist rejected items so the UI can offer a "Restore" action.
    for item in result.get("rejected", []) or []:
        raw_text = (item.get("raw_text") or "").strip()
        if not raw_text:
            continue
        session.add(RejectedQuestion(
            bank_id=bank_id,
            book_id=book_id,
            section_ref=unit.id,
            section_title=unit.title,
            page_start=item.get("page") or unit.page_start,
            page_end=unit.page_end,
            raw_text=raw_text,
            reject_reason=item.get("_reject_reason") or "",
            payload=item,
            status="pending",
        ))

    inserted = 0
    latex_agg = QuestionLatexReport()
    for item in result.get("extracted", []):
        raw_text = (item.get("raw_text") or "").strip()
        if not raw_text:
            continue
        # KaTeX normalization: clean inline math spans, wrap bare Unicode math
        # glyphs + obvious chemistry. Options live inline in raw_text.
        raw_text, solution_text, rep = _normalize_question_fields(
            raw_text, item.get("solution") or None
        )
        latex_agg = latex_agg + rep
        # Q1 invariant: has_solution == (solution_text non-empty). Finalize
        # AFTER normalize so we judge the cleaned text.
        solution_text, has_solution = _finalize_solution_flag(solution_text)
        # Q6 Part D — a question that failed strict-substring verification is
        # KEPT (never silently dropped) but marked needs-review so the UI can
        # surface it. Clean questions keep the historical pass payload.
        low_conf = bool(item.get("_q3_low_confidence"))
        if low_conf:
            qc_local = {
                "pass": False,
                "score": 0.5,
                "needs_review": True,
                "failures": ["verify_low_confidence"],
                "reason": item.get("_q3_verify_reason") or "",
            }
            qc_status_val = "flagged"
        else:
            qc_local = {"pass": True, "score": 1.0, "failures": []}
            qc_status_val = "pending"
        q = Question(
            bank_id=bank_id,
            book_id=book_id,
            section_ref=unit.id,
            section_uuid=section_uuid,  # Phase 2: canonical FK alongside slug
            section_title=unit.title,
            page_start=item.get("page") or unit.page_start,
            page_end=unit.page_end,
            raw_text=raw_text,
            qc_local=qc_local,
            qc_status=qc_status_val,
            attempts=int(result.get("attempts") or 1),
            status="passed",
            question_number=item.get("question_number"),
            exercise_ref=item.get("exercise_ref"),
            kind=str(item.get("kind") or "exercise"),
            has_options=bool(item.get("has_options")),
            solution_text=solution_text,
            has_solution=has_solution,
            identified_total=int(result.get("identified_total") or 0),
        )
        session.add(q)
        inserted += 1
    if inserted and (
        latex_agg.glyphs_wrapped
        or latex_agg.chemistry_wrapped
        or latex_agg.spans_normalized
        or latex_agg.cases_fixed
        or latex_agg.braces_repaired
    ):
        logger.info(
            "latex_normalize unit=%s spans=%d glyphs=%d chem=%d cases=%d braces=%d",
            unit.id,
            latex_agg.spans_normalized,
            latex_agg.glyphs_wrapped,
            latex_agg.chemistry_wrapped,
            latex_agg.cases_fixed,
            latex_agg.braces_repaired,
        )
    session.commit()
    return inserted


# ---------------------------------------------------------------------------
# Status classification — drives the per-section badge in the UI.
# ---------------------------------------------------------------------------
def _legacy_block_status(v3_status: str) -> str:
    """Map v3's per-section status to the legacy block status the UI expects.

    v3 → legacy:
        complete → ok
        partial  → partial   (already understood by the type union)
        empty    → empty
        failed   → failed
    """
    return {"complete": "ok", "skipped": "empty"}.get(v3_status, v3_status)


# Sections that the EXTRACTOR PROMPT correctly excludes — crosswords,
# activity boxes, hint/note callouts, etc. The analyser may have given
# them an `expected` count > 0 (it counts visible numbered items), but
# the prompt rules return 0 questions, so the gap is NOT a real miss.
# Used to suppress the bogus "14 missed" inflation seen on Crossword.
#
# Match is case-insensitive substring on section_title; keep terms
# narrow enough to avoid colliding with real section names. Update if
# the prompt's exclusion list grows.
_NON_QUESTION_TITLE_PATTERNS = (
    "crossword",
    "word search",
    "word-search",
    "jumble",
    "fill in the blank",
    "fill-in-the-blank",
    "try it",
    "try-it",
    "quick check",
    "quick-check",
    "self check",
    "self-check",
    "check your understanding",
    "progress check",
    "test yourself",
    "did you know",
    "fun fact",
    "thinking corner",
    "activity",          # "Activity", "Activity 5.1" — hands-on theory aid
    "illustration",      # "Illustration N" — worked walk-through, not a Q
)


def _is_intentional_non_question_block(title: str | None) -> bool:
    if not title:
        return False
    t = title.strip().lower()
    return any(pat in t for pat in _NON_QUESTION_TITLE_PATTERNS)


def _classify_unit(
    expected: int | None, kept: int, identified: int, ok: bool
) -> str:
    """Return one of: "complete" | "partial" | "empty" | "failed".

    "complete" — extraction succeeded and (no expected count OR kept >= expected)
    "partial"  — extraction succeeded but kept < expected (missing questions)
    "empty"    — extraction succeeded with zero items (which is fine if expected==0)
    "failed"   — extraction call errored after retries
    """
    if not ok:
        return "failed"
    if kept == 0:
        return "empty" if (expected or 0) == 0 else "partial"
    if expected and kept < expected:
        return "partial"
    return "complete"


# ---------------------------------------------------------------------------
# Q-1 figure-witness retry — rescue questions silently dropped because they
# contain inline figures. Uses the figure extractor's regen_meta.question_no
# field as the witness for "which question_numbers MUST exist on which pages."
# ---------------------------------------------------------------------------
_QNO_PREFIX_RE = re.compile(r"^q\.?\s*", re.IGNORECASE)


def _norm_qno(s) -> str:
    """Normalize a question number for set comparison. Handles the
    cosmetic differences between Gemini's figure-extractor output
    (``Q.39``, ``(39)``, ``39.``) and the question worker's stored
    ``question_number`` field (``39``). Mirrors the same normalizer
    used by the figure embedder."""
    if not s:
        return ""
    t = str(s).strip().lower()
    t = _QNO_PREFIX_RE.sub("", t)
    return t.strip("().[]{} \t.")


async def _retry_missing_questions_from_figures(
    book_id: UUID,
    bank_id: UUID,
    units: list["_Unit"],
    pdf_bytes: bytes,
    system_prompt: str,
) -> None:
    """If figure.regen_meta.question_no points at a question that doesn't
    exist in the DB, re-call the question worker on the affected section
    with a focused prompt naming the missing q_nos.

    Multi-column safe: the unit's existing ``section_start_heading`` is
    preserved on retry, so the BOUNDARY hint still flows into the prompt.
    """
    from app.models.figure import Figure
    from app.models.question import Question

    # 1. Build figure-witness map: page_number → set of question_nos
    with SyncSession() as session:
        figures = session.execute(
            select(Figure).where(Figure.book_id == book_id)
        ).scalars().all()

        # Pull question_no from regen_meta where present + context=question.
        witness_qnos: set[str] = set()
        qnos_with_page: list[tuple[str, int | None]] = []
        for f in figures:
            ctx = (f.context_hint or "").lower()
            meta = f.regen_meta if isinstance(f.regen_meta, dict) else None
            if not meta or ctx != "question":
                continue
            qno = (meta.get("question_no") or "").strip()
            if not qno:
                continue
            witness_qnos.add(qno)
            qnos_with_page.append((qno, f.page_number))

        if not witness_qnos:
            logger.info(
                "[q-retry] no figure witnesses for book=%s — skipping",
                book_id,
            )
            return

        # 2. Read currently-extracted question_numbers from DB.
        existing_qrows = session.execute(
            select(Question).where(
                Question.bank_id == bank_id,
                Question.regen_id.is_(None),
            )
        ).scalars().all()
        existing_qnos = {
            _norm_qno(q.question_number) for q in existing_qrows
            if q.question_number
        }

    # 3. Compute missing — every witness q_no that's NOT in DB.
    missing_qnos = {
        q for q in witness_qnos if _norm_qno(q) not in existing_qnos
    }
    if not missing_qnos:
        logger.info(
            "[q-retry] all %d figure witnesses already have questions in DB "
            "(book=%s) — nothing to retry",
            len(witness_qnos), book_id,
        )
        return

    # 4. Group missing q_nos by section (via figure.page → unit page-range).
    section_missing: dict[str, list[str]] = {}
    for qno, page in qnos_with_page:
        if qno not in missing_qnos:
            continue
        if page is None:
            continue
        for u in units:
            if u.page_start is None or u.page_end is None:
                continue
            if u.page_start <= page <= u.page_end:
                section_missing.setdefault(u.id, []).append(qno)
                break

    if not section_missing:
        logger.warning(
            "[q-retry] %d missing q_nos but couldn't map to any unit "
            "(book=%s) — likely page→section gap; skipping",
            len(missing_qnos), book_id,
        )
        return

    logger.info(
        "[q-retry] book=%s — %d sections have missing figure-witness "
        "questions: %s",
        book_id, len(section_missing),
        {sid: sorted(set(qs)) for sid, qs in section_missing.items()},
    )

    # 5. For each affected section, retry + persist (insert-only merge).
    units_by_id = {u.id: u for u in units}
    inserted_total = 0
    still_missing_total = 0
    for sid, qnos in section_missing.items():
        unit = units_by_id.get(sid)
        if unit is None:
            continue
        try:
            result = await _run_targeted_retry(
                unit=unit,
                pdf_bytes=pdf_bytes,
                system_prompt=system_prompt,
                missing_qnos=sorted(set(qnos)),
            )
        except Exception as e:
            logger.warning(
                "[q-retry] targeted retry failed for section=%s: %s",
                sid, e,
            )
            continue

        if not result.get("ok") or not result.get("extracted"):
            still_missing_total += len(set(qnos))
            logger.warning(
                "[q-retry] section=%s retry returned 0 questions for "
                "missing q_nos %s — leaving as gap",
                sid, sorted(set(qnos)),
            )
            continue

        # Insert-only merge: only add items whose question_number isn't
        # already in DB (avoid wiping existing rows or duplicating).
        ins = _insert_only_merge(
            book_id=book_id, bank_id=bank_id, unit=unit, result=result,
        )
        inserted_total += ins
        logger.info(
            "[q-retry] section=%s inserted %d rescued question(s)",
            sid, ins,
        )

    logger.info(
        "[q-retry] book=%s — done. inserted=%d, still_missing=%d",
        book_id, inserted_total, still_missing_total,
    )


# ───────────────────────────────────────────────────────────────────────
# Q-3 — Page-by-page undercount fallback (Task 1 Pass 3)
# ───────────────────────────────────────────────────────────────────────
#
# Runs AFTER Q-1 (figure-witness) + Q-2 (solution-completeness) have done
# their best. For any Cat A unit that's STILL incomplete
# (extracted < COMPLETENESS_THRESHOLD × expected), scans each page of the
# unit's range in a SEPARATE Gemini call. The intuition: Gemini sometimes
# undercounts on long sections because attention degrades when scanning
# many questions in one call. Constraining the prompt to ONE page makes
# each pass exhaustive.
#
# Cost gates (HARD):
#   MAX_UNITS_PER_BOOK   = 20   — at most 20 incomplete units retried
#   MAX_PAGES_PER_UNIT   = 12   — at most 12 page-calls per unit
#   MIN_UNIT_PAGE_SPAN   = 2    — single-page units already minimal, skip
#
# Dedup: new questions are merged by question_number against existing
# DB rows. Insertion uses the same _insert_only_merge path as Q-1.
#
# Idempotent: re-running adds zero rows when the gap is already closed.

_PAGE_BY_PAGE_MAX_UNITS_PER_BOOK = 20
_PAGE_BY_PAGE_MAX_PAGES_PER_UNIT = 12
_PAGE_BY_PAGE_MIN_UNIT_PAGE_SPAN = 2


async def _run_single_page_scan(
    unit: "_Unit",
    pdf_bytes: bytes,
    system_prompt: str,
    page_number: int,
    already_extracted_qnos: set[str],
) -> dict[str, Any]:
    """Scan ONE page of a unit. Returns {"ok", "extracted", "identified_total"}.

    The prompt explicitly names question_numbers already in DB so Gemini
    skips re-extracting them — keeps the response tight + the dedup pass
    cheap.
    """
    pdf_slice = _slice_pdf(pdf_bytes, page_number, page_number)

    already_str = ", ".join(sorted(already_extracted_qnos)[:80]) if already_extracted_qnos else "(none)"
    user_prompt = (
        f"PAGE-BY-PAGE UNDERCOUNT RECOVERY for section: \"{unit.title}\" "
        f"(ID: {unit.id}).\n\n"
        f"You are looking at page {page_number} ONLY. Extract every numbered "
        f"question / example / exercise / problem PRINTED on this exact page.\n\n"
        f"Already extracted on prior passes (skip these — do NOT re-emit): "
        f"{already_str}.\n\n"
        f"Rules:\n"
        f"- OCR-only — verbatim text, no fabrication, no invented options.\n"
        f"- If a question's stem spans this page and continues onto the next, "
        f"emit it as belonging to THIS page (its starting page).\n"
        f"- If a numbered item appears here AND in 'already extracted', OMIT "
        f"it — duplicates corrupt the bank.\n"
        f"- If the page has NO new questions, return identified_total=0 with "
        f"`extracted` as an empty array.\n\n"
        f"Return JSON: section_id=\"{unit.id}\", section_title=\"{unit.title}\", "
        f"identified_total (count you found newly on this page), "
        f"extracted (array of only the new items)."
    )
    try:
        raw = await _gemini_call_with_transient_retries(
            pdf_slice, system_prompt, user_prompt,
            ctx=f"q3-page {unit.kind}/{unit.id}/p{page_number}",
        )
        data = parse_json(raw)
        if not isinstance(data, dict):
            return {"ok": False, "extracted": [], "identified_total": 0}
        extracted = list(data.get("extracted") or [])
        fr = filter_items(extracted)
        return {
            "ok": True,
            "identified_total": int(data.get("identified_total") or 0),
            "extracted": fr.kept,
        }
    except Exception as e:
        logger.warning(
            "Q-3 single-page scan failed (unit=%s page=%d): %s",
            unit.id, page_number, e,
        )
        return {"ok": False, "extracted": [], "identified_total": 0}


async def _retry_undercount_page_by_page(
    book_id: UUID,
    bank_id: UUID,
    units: list["_Unit"],
    pdf_bytes: bytes,
    system_prompt: str,
) -> None:
    """Last-resort recovery for units still under COMPLETENESS_THRESHOLD
    after the figure-witness + solution-completeness retries.

    For each qualifying incomplete unit, runs one Gemini call per page in
    its range. Each call is scoped to a single page so Gemini's attention
    can't dilute across many questions. New rows are dedup-by-q_no
    against the bank's existing questions before insert.

    HARD-gated by per-book + per-unit caps to bound Gemini cost.
    Idempotent — a follow-up run with no remaining gap is a no-op.
    """
    from app.models.question import Question

    # 1. Snapshot per-unit extracted counts + existing q_nos.
    with SyncSession() as session:
        rows = session.execute(
            select(
                Question.section_ref,
                Question.question_number,
            ).where(
                Question.bank_id == bank_id,
                Question.regen_id.is_(None),
            )
        ).all()

    extracted_per_section: dict[str, int] = {}
    qnos_per_section: dict[str, set[str]] = {}
    for sref, qno in rows:
        if not sref:
            continue
        extracted_per_section[sref] = extracted_per_section.get(sref, 0) + 1
        if qno:
            qnos_per_section.setdefault(sref, set()).add(_norm_qno(qno))

    # 2. Identify incomplete units that qualify for page-by-page retry.
    candidates: list["_Unit"] = []
    for u in units:
        if u.expected is None:
            continue
        expected = int(u.expected or 0)
        if expected <= 0:
            continue
        extracted = extracted_per_section.get(u.id, 0)
        if not _is_incomplete(expected, extracted):
            continue
        if u.page_start is None or u.page_end is None:
            continue
        page_span = (u.page_end - u.page_start) + 1
        if page_span < _PAGE_BY_PAGE_MIN_UNIT_PAGE_SPAN:
            # Single-page unit — Gemini's first pass already scanned it
            # exhaustively; another single-page call adds no signal.
            continue
        candidates.append(u)

    if not candidates:
        logger.info(
            "[q3-page] book=%s — no incomplete multi-page units, skipping",
            book_id,
        )
        return

    # Cap candidate count globally. Sort by largest gap first so we spend
    # the budget on the worst undercounts.
    def _gap(u: "_Unit") -> int:
        return int(u.expected or 0) - extracted_per_section.get(u.id, 0)
    candidates.sort(key=_gap, reverse=True)
    if len(candidates) > _PAGE_BY_PAGE_MAX_UNITS_PER_BOOK:
        logger.info(
            "[q3-page] book=%s — %d incomplete units; capping to %d biggest "
            "gaps (skipping the rest this pass)",
            book_id, len(candidates), _PAGE_BY_PAGE_MAX_UNITS_PER_BOOK,
        )
        candidates = candidates[:_PAGE_BY_PAGE_MAX_UNITS_PER_BOOK]

    logger.info(
        "[q3-page] book=%s — page-by-page retry for %d incomplete unit(s)",
        book_id, len(candidates),
    )

    total_inserted = 0
    for u in candidates:
        existing_qnos = set(qnos_per_section.get(u.id, set()))
        pages = list(range(u.page_start, u.page_end + 1))
        if len(pages) > _PAGE_BY_PAGE_MAX_PAGES_PER_UNIT:
            logger.info(
                "[q3-page] unit=%s spans %d pages — capping per-unit scan "
                "to first %d pages (worker can re-run later for the tail)",
                u.id, len(pages), _PAGE_BY_PAGE_MAX_PAGES_PER_UNIT,
            )
            pages = pages[:_PAGE_BY_PAGE_MAX_PAGES_PER_UNIT]

        unit_inserted = 0
        for p in pages:
            res = await _run_single_page_scan(
                unit=u, pdf_bytes=pdf_bytes,
                system_prompt=system_prompt,
                page_number=p,
                already_extracted_qnos=existing_qnos,
            )
            if not res.get("ok"):
                continue
            page_items = res.get("extracted") or []
            if not page_items:
                continue
            # Stash a synthetic "result" so _insert_only_merge can do its
            # dedup-by-q_no + persist for us. identified_total reflects only
            # this page's count.
            inserted = _insert_only_merge(
                book_id=book_id, bank_id=bank_id, unit=u,
                result={
                    "extracted": page_items,
                    "identified_total": int(res.get("identified_total") or 0),
                },
            )
            if inserted:
                unit_inserted += inserted
                # Update the local already-seen set so the next page's
                # Gemini call won't be asked to re-emit these.
                for it in page_items:
                    qn = it.get("question_number")
                    if qn:
                        existing_qnos.add(_norm_qno(qn))

        logger.info(
            "[q3-page] unit=%s rescued=%d on %d page(s) "
            "(expected=%s, was_extracted=%d, now=%d)",
            u.id, unit_inserted, len(pages), u.expected,
            extracted_per_section.get(u.id, 0),
            extracted_per_section.get(u.id, 0) + unit_inserted,
        )
        total_inserted += unit_inserted

    logger.info(
        "[q3-page] book=%s — done. units=%d total_inserted=%d",
        book_id, len(candidates), total_inserted,
    )

    # ─── Q-3 VERIFICATION LOOP (Tier A safety net) ───────────────────
    # After Pass-3 finishes, re-count every candidate against its expected
    # and surface what's STILL missing. Without this loop, a Pass-3 call
    # that also misses (Gemini's per-page scan failed to find the
    # specific stragglers) silently passes through — operator never
    # knows the gap remains. With it: a structured warning is logged
    # for every section that didn't fully recover, listing the gap and
    # the page range so a manual re-extract has the info it needs.
    #
    # This is observability only — does NOT trigger another Gemini call.
    # The page-by-page pass already exhausted its budget; a third pass
    # with the same strategy wouldn't add signal.
    try:
        still_incomplete: list[tuple[str, int, int]] = []  # (section_id, got, expected)
        with SyncSession() as _sess:
            for u in candidates:
                expected = int(u.expected or 0)
                if expected <= 0:
                    continue
                final_got = _sess.execute(
                    select(func.count(Question.id)).where(
                        Question.book_id == book_id,
                        Question.bank_id == bank_id,
                        Question.regen_id.is_(None),
                        Question.section_ref == u.id,
                    )
                ).scalar_one()
                if final_got < expected:
                    still_incomplete.append((u.id, final_got, expected))

        if still_incomplete:
            logger.warning(
                "[q3-verify] book=%s — %d section(s) STILL incomplete after "
                "Pass-3 page-by-page recovery: %s",
                book_id, len(still_incomplete),
                [
                    f"{sid}={got}/{exp} (gap={exp - got})"
                    for sid, got, exp in still_incomplete
                ],
            )
        else:
            logger.info(
                "[q3-verify] book=%s — all %d retried section(s) recovered "
                "to expected count ✓",
                book_id, len(candidates),
            )
    except Exception as e:
        # Never let verification telemetry break the main extraction flow.
        logger.warning("[q3-verify] book=%s — verification pass failed: %s",
                       book_id, e)


def _insert_only_merge(
    book_id: UUID,
    bank_id: UUID,
    unit: "_Unit",
    result: dict[str, Any],
) -> int:
    """Insert questions from a retry result WITHOUT wiping existing rows.

    Skips items whose question_number already exists for this bank, so
    repeated retries don't create duplicates. Returns count inserted.
    """
    from app.services.section_identity import resolve_section_uuid as _resolve_sec_uuid

    with SyncSession() as session:
        existing_qnos_for_bank = {
            _norm_qno(qn) for (qn,) in session.execute(
                select(Question.question_number).where(
                    Question.bank_id == bank_id,
                    Question.regen_id.is_(None),
                )
            ).all() if qn
        }

        section_uuid_val = _resolve_sec_uuid(session, book_id, unit.id)

        inserted = 0
        for item in result.get("extracted") or []:
            raw_text = (item.get("raw_text") or "").strip()
            if not raw_text:
                continue
            qno = item.get("question_number")
            if qno and _norm_qno(qno) in existing_qnos_for_bank:
                continue  # already in DB, don't duplicate
            # KaTeX normalization (same seam as _persist_unit).
            raw_text, solution_text, _rep = _normalize_question_fields(
                raw_text, item.get("solution") or None
            )
            # Q1 invariant: finalize flag AFTER normalize.
            solution_text, has_solution = _finalize_solution_flag(solution_text)
            q = Question(
                bank_id=bank_id,
                book_id=book_id,
                section_ref=unit.id,
                section_uuid=section_uuid_val,
                section_title=unit.title,
                page_start=item.get("page") or unit.page_start,
                page_end=unit.page_end,
                raw_text=raw_text,
                qc_local={"pass": True, "score": 1.0, "failures": [], "rescued_by": "q1-retry"},
                attempts=1,
                status="passed",
                question_number=qno,
                exercise_ref=item.get("exercise_ref"),
                kind=str(item.get("kind") or "exercise"),
                has_options=bool(item.get("has_options")),
                solution_text=solution_text,
                has_solution=has_solution,
                identified_total=int(result.get("identified_total") or 0),
            )
            session.add(q)
            if qno:
                existing_qnos_for_bank.add(_norm_qno(qno))
            inserted += 1
        session.commit()
        return inserted


# ---------------------------------------------------------------------------
# Q-2 solution-completeness retry — rescue missing solution_text where a
# question claims has_solution=true but solution_text is empty. Item-level
# retry; UPDATE existing rows (don't INSERT — questions already exist).
# ---------------------------------------------------------------------------
async def _run_solution_retry(
    unit: "_Unit",
    pdf_bytes: bytes,
    system_prompt: str,
    qnos_needing_solution: list[str],
    worked_example: bool = False,
) -> dict[str, Any]:
    """Re-OCR ONLY the solutions for the listed question numbers.

    When ``worked_example`` is True the prompt asserts that a COMPLETE
    printed solution is guaranteed to exist directly below each problem
    (B3 — solution-completeness gate), demanding the full verbatim
    transcription. The no-fabrication escape hatch is preserved either way.

    Returns ``{"ok": True, "items": [{question_number, solution}, ...]}``
    on success, or ``{"ok": False, "items": []}`` on failure.
    """
    is_example = bool(unit.id) and "example" in (unit.id or "").lower()
    is_tight = (
        unit.page_start is not None
        and unit.page_end is not None
        and unit.page_end - unit.page_start <= 0
    )
    leading_pad = 1 if (
        is_example and is_tight and unit.page_start and unit.page_start > 1
    ) else 0
    start = (unit.page_start - leading_pad) if unit.page_start is not None else None
    padded_end = _effective_slice_end(unit.page_end, unit.next_page_start)
    pdf_slice = _slice_pdf(pdf_bytes, start, padded_end)

    qnos_str = ", ".join(qnos_needing_solution[:50])
    worked_addendum = (
        "\nThis is a WORKED EXAMPLE / SOLVED PROBLEM section. The COMPLETE "
        "solution is printed in the PDF immediately below (or beside) each "
        "problem statement. You MUST extract the full solution VERBATIM — "
        "every step, equation, and final answer. Do NOT skip or summarize. "
        "If — and only if — a solution is genuinely not printed for a listed "
        "question, OMIT it (never fabricate).\n"
    ) if worked_example else ""
    user_prompt = (
        f"SOLUTION RECOVERY pass for section: \"{unit.title}\" (ID: {unit.id}).\n\n"
        f"On a previous pass, the following question numbers were extracted but "
        f"their printed solution text was NOT transcribed: {qnos_str}.\n"
        f"{worked_addendum}\n"
        f"Re-scan the pages. For each listed question_number, locate the printed "
        f"SOLUTION / ANSWER / WORKED-OUT text that appears below, beside, or "
        f"adjacent to that question on the page. Transcribe it VERBATIM into a "
        f"`solution` field. Apply OCR-only rules — never compute, complete, "
        f"derive, or supply a missing step. Never use training knowledge to "
        f"fill in what is illegible.\n\n"
        f"If a listed question genuinely has no printed solution visible on the "
        f"page, OMIT it from the response (do NOT fabricate). If the printed "
        f"solution is partially illegible, transcribe what is readable and stop.\n\n"
        f"Return JSON: {{\"section_id\": \"{unit.id}\", \"items\": "
        f"[{{\"question_number\": \"<as printed>\", \"solution\": "
        f"\"<verbatim solution text>\"}}, ...]}}"
    )
    try:
        raw = await _gemini_call_with_transient_retries(
            pdf_slice, system_prompt, user_prompt,
            ctx=f"q2-solution-retry {unit.kind}/{unit.id}",
        )
        data = parse_json(raw)
        if not isinstance(data, dict):
            return {"ok": False, "items": []}
        items = list(data.get("items") or data.get("extracted") or [])
        # Sanitize: only keep items with both question_number and non-empty solution
        clean: list[dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            qn = (it.get("question_number") or "").strip()
            sol = (it.get("solution") or "").strip()
            if not qn or not sol:
                continue
            clean.append({"question_number": qn, "solution": sol})
        return {"ok": True, "items": clean}
    except Exception as e:
        logger.warning("Q2 solution retry failed for %s/%s: %s",
                       unit.kind, unit.id, e)
        return {"ok": False, "items": []}


async def _retry_missing_solutions(
    book_id: UUID,
    bank_id: UUID,
    units: list["_Unit"],
    pdf_bytes: bytes,
    system_prompt: str,
) -> None:
    """Rescue questions that are missing their printed solution. Two
    conditions are scanned, both retried per-section (max 1 retry/section):

      (1) DATA-INCONSISTENCY — the model claimed has_solution=true but
          solution_text is empty (companion to the Q1 invariant).
      (2) SOLUTION-COMPLETENESS GATE (B2) — a worked-example / solved-problem
          section (``_section_implies_solution``) whose question lacks any
          solution at all. Worked examples ALWAYS print a complete solution,
          so an empty one is an extraction miss. These sections get the
          stronger "extract the FULL printed solution" prompt (B3).

    Plain exercises / MCQ banks without printed solutions are NEVER scanned
    under (2) — conditional guard, no fabrication.
    """
    # 1. Find affected questions in DB.
    with SyncSession() as session:
        all_qs = session.execute(
            select(Question).where(
                Question.book_id == book_id,
                Question.bank_id == bank_id,
                Question.regen_id.is_(None),
            )
        ).scalars().all()

    affected: list[Question] = []
    worked_sections: set[str] = set()
    solution_incomplete_count = 0
    for q in all_qs:
        if (q.solution_text or "").strip():
            continue  # already has a solution — nothing to do
        is_worked = _section_implies_solution(
            q.section_title, q.section_ref, q.kind
        )
        if is_worked:
            # B2: worked example missing its guaranteed solution.
            solution_incomplete_count += 1
            worked_sections.add(q.section_ref)
            affected.append(q)
        elif q.has_solution:
            # Data-inconsistency: flag=true but empty text (pre-Q1 rows or a
            # stray model claim). Retry to honor the claim.
            affected.append(q)

    if solution_incomplete_count:
        logger.info(
            "[solution-gate] book=%s — %d worked-example question(s) missing "
            "their printed solution (will retry)", book_id, solution_incomplete_count,
        )

    if not affected:
        logger.info(
            "[q2-solution-retry] book=%s — no questions with missing "
            "solution_text — nothing to retry", book_id,
        )
        return

    # 2. Group by section_ref
    by_section: dict[str, list[str]] = {}
    for q in affected:
        if not q.question_number:
            continue
        by_section.setdefault(q.section_ref, []).append(q.question_number)

    if not by_section:
        logger.info(
            "[q2-solution-retry] book=%s — %d affected questions have no "
            "question_number, cannot retry safely", book_id, len(affected),
        )
        return

    logger.info(
        "[q2-solution-retry] book=%s — %d sections, %d total questions "
        "need solution recovery",
        book_id, len(by_section), len(affected),
    )

    # 3. For each section, retry + update
    units_by_id = {u.id: u for u in units}
    updated_total = 0
    for sid, qnos in by_section.items():
        unit = units_by_id.get(sid)
        if unit is None:
            logger.warning(
                "[q2-solution-retry] section=%s not in units list, skipping",
                sid,
            )
            continue
        try:
            result = await _run_solution_retry(
                unit=unit,
                pdf_bytes=pdf_bytes,
                system_prompt=system_prompt,
                qnos_needing_solution=sorted(set(qnos)),
                worked_example=sid in worked_sections,
            )
        except Exception as e:
            logger.warning(
                "[q2-solution-retry] retry failed for section=%s: %s",
                sid, e,
            )
            continue

        items = result.get("items") or []
        if not result.get("ok") or not items:
            logger.info(
                "[q2-solution-retry] section=%s — retry returned 0 solutions",
                sid,
            )
            continue

        # 4. UPDATE existing question rows
        items_by_qno = {_norm_qno(it["question_number"]): it["solution"] for it in items}
        with SyncSession() as session:
            section_qs = session.execute(
                select(Question).where(
                    Question.bank_id == bank_id,
                    Question.section_ref == sid,
                    Question.regen_id.is_(None),
                )
            ).scalars().all()
            updated_here = 0
            for q in section_qs:
                if not q.question_number:
                    continue
                if (q.solution_text or "").strip():
                    continue  # already has solution; don't overwrite
                sol = items_by_qno.get(_norm_qno(q.question_number))
                if sol:
                    sol_norm, _ = normalize_question_latex(sol)
                    # Q1 invariant: keep solution_text + has_solution in lockstep.
                    q.solution_text, q.has_solution = _finalize_solution_flag(sol_norm)
                    if q.has_solution:
                        updated_here += 1
            session.commit()
            updated_total += updated_here
            logger.info(
                "[q2-solution-retry] section=%s — updated %d solution(s)",
                sid, updated_here,
            )

    logger.info(
        "[q2-solution-retry] book=%s — done. solutions rescued=%d",
        book_id, updated_total,
    )


# ---------------------------------------------------------------------------
# Main task
# ---------------------------------------------------------------------------
async def _run_v3(book_id: UUID, bank_id: UUID, job_id: UUID) -> dict[str, Any]:
    with SyncSession() as session:
        book = session.get(Book, book_id)
        if book is None:
            raise ValueError(f"Book {book_id} not found")
        bank = session.get(QuestionBank, bank_id)
        if bank is None:
            raise ValueError(f"Bank {bank_id} not found")
        if not book.schema:
            raise ValueError("Book has no approved schema — analyse first")

        schema = BookSchema(**book.schema)
        # Read upload-time multi-column flag from book.analyser. When
        # True, units carry the printed section heading as
        # `section_start_heading` so the extractor can skip the tail of
        # the previous section on a shared boundary page. Default False
        # → single-column prompt path stays byte-identical.
        is_multi_column = bool((book.analyser or {}).get("is_multi_column", False))
        units = _flatten_sections(schema, is_multi_column=is_multi_column)
        total = len(units)
        if total == 0:
            _update_bank(session, bank_id, status="ready",
                         extraction_stats={"sections": [], "totals": {
                             "expected_total": 0, "extracted_total": 0,
                             "complete": 0, "partial": 0, "empty": 0, "failed": 0,
                         }})
            _update_job(session, job_id, status="succeeded", progress=100,
                        message="No question-bearing sections in schema",
                        finished_at=datetime.utcnow())
            return {"ok": True, "sections_processed": 0}

        pdf_bytes = download_pdf(book.pdf_url or "")
        system_prompt = load_raw("question_extractor_v3")

        _update_bank(session, bank_id, status="extracting")
        _update_job(session, job_id, status="running", progress=5,
                    message=f"v3 extraction — {total} section(s) to process")

    # Lever 2 — run units in parallel. The gemini_runtime semaphore caps
    # in-flight calls at 4, so this is safe wrt quota. Persistence and stats
    # updates happen sequentially in the main coroutine as each unit finishes
    # (via asyncio.as_completed), keeping SQLite single-writer-safe.
    #
    # Lever 1 — units flagged unit.skipped (eqc=0) bypass the Gemini call
    # entirely and just record a "skipped" stats row, so the user can still
    # see them in the UI and click "↺ Retry section" on any one.
    section_reports: list[dict[str, Any]] = []
    expected_total = 0
    extracted_total = 0
    counts = {"complete": 0, "partial": 0, "empty": 0, "failed": 0, "skipped": 0}

    async def _process(unit: _Unit) -> tuple[_Unit, dict[str, Any]]:
        if unit.skipped:
            return unit, {
                "ok": True, "attempts": 0, "identified_total": 0,
                "extracted": [], "rejected": [], "rejected_count": 0,
                "_skipped": True,
            }
        # Q3: extract → verify → targeted retry (1x) for excluded sections.
        result = await _extract_unit_with_verify_and_retry(
            unit, pdf_bytes, system_prompt,
        )
        return unit, result

    # Heartbeat keeps the watchdog quiet while units are still in flight.
    # The slowest unit on a scanned PDF can run >300s, and during that
    # window no completions happen → no _update_job → watchdog kill.
    # The 10s daemon thread pings last_heartbeat_at independently of
    # unit completions. It only writes the timestamp/progress field;
    # no Gemini, no extraction logic touched.
    with Heartbeat(
        job_id,
        base_msg=f"Extracting questions ({total} sections)",
        progress=5,
    ):
        tasks = [asyncio.create_task(_process(u)) for u in units]
        done = 0
        for coro in asyncio.as_completed(tasks):
            unit, result = await coro
            done += 1
            progress = 5 + int(90 * done / max(total, 1))
            with SyncSession() as session:
                _update_job(
                    session, job_id,
                    progress=progress,
                    message=f"Extracted {unit.title} ({done}/{total})",
                )

            sol_incomplete = 0
            if result.get("_skipped"):
                kept = 0
                identified = 0
                status = "skipped"
            else:
                kept = len(result.get("extracted") or [])
                identified = int(result.get("identified_total") or 0)
                status = _classify_unit(unit.expected, kept, identified, bool(result.get("ok")))

                # B2 — solution-completeness gate: count worked-example items
                # that came back with no printed solution. The dedicated retry
                # pass (_retry_missing_solutions) rescues these afterwards; this
                # is the per-unit telemetry so the count is visible in stats.
                if _section_implies_solution(unit.title, unit.id, unit.kind):
                    for it in result.get("extracted") or []:
                        if not (it.get("solution") or "").strip():
                            sol_incomplete += 1

                with SyncSession() as session:
                    _persist_unit(session, bank_id, book_id, unit, result)

            # Non-question blocks (Crossword / Activity / Try-It / etc.):
            # don't count them in ANY headline metric. They're shown in
            # the per-section list so users see what was on the page,
            # but they don't add to:
            #   - expected_total (would inflate the denominator)
            #   - extracted_total (they correctly produce 0 items)
            #   - status counters (complete / partial / empty / failed)
            # The prompt is supposed to return 0 from them, and that's
            # the desired behavior — not a "missed section".
            if _is_intentional_non_question_block(unit.title):
                pass  # do not contribute to any count
            else:
                counts[status] = counts.get(status, 0) + 1
                expected_total += int(unit.expected or 0)
                extracted_total += kept

            section_reports.append({
                "section_ref": unit.id,
                "section_title": unit.title,
                "kind": unit.kind,
                "page_start": unit.page_start,
                "page_end": unit.page_end,
                "expected": unit.expected,
                "identified": identified,
                "extracted": kept,
                "rejected": result.get("rejected_count", 0),
                "rejected_items": result.get("rejected") or [],
                "status": status,
                "attempts": result.get("attempts", 0),
                "error": result.get("error"),
                # B2 telemetry: worked-example items missing a printed solution.
                "solution_incomplete": sol_incomplete,
                # Q6 Part A telemetry — completeness gate per unit.
                "completeness_incomplete": bool(result.get("completeness_incomplete")),
                "completeness_gap": int(result.get("completeness_gap") or 0),
                "completeness_retries": int(result.get("completeness_retries") or 0),
            })

            # Persist rolling stats so the UI can show progress mid-run. Sort by
            # page so the order is stable across parallel completions (otherwise
            # the section list would reshuffle on every refresh).
            sorted_reports = sorted(
                section_reports,
                key=lambda s: (s.get("page_start") or 0, s.get("page_end") or 0),
            )
            legacy_blocks = [
                {
                    "excluded_block_index": idx,
                    "title": s["section_title"],
                    "section_ref": s["section_ref"],
                    "page_start": s["page_start"],
                    "page_end": s["page_end"],
                    "identified": s["identified"],
                    "extracted": s["extracted"],
                    # Non-question blocks (Crossword / Activity / Try-It …)
                    # contribute 0 to missed — the prompt is supposed to
                    # return 0 from them. See helper for the title list.
                    "missed": (
                        0
                        if _is_intentional_non_question_block(s.get("section_title"))
                        else max(
                            0,
                            (s.get("expected") or s["identified"]) - s["extracted"],
                        )
                    ),
                    "status": _legacy_block_status(s["status"]),
                }
                for idx, s in enumerate(sorted_reports)
            ]
            with SyncSession() as session:
                _update_bank(session, bank_id, extraction_stats={
                    "sections": sorted_reports,
                    "totals": {
                        "expected_total": expected_total,
                        "extracted_total": extracted_total,
                        **counts,
                    },
                    "blocks": legacy_blocks,
                    "total_identified": sum(s["identified"] for s in sorted_reports),
                    "total_extracted": extracted_total,
                    "missed": sum(b["missed"] for b in legacy_blocks),
                    # B2: aggregate worked-example items missing a solution
                    # (pre-retry). Surfaced so the gate is observable in stats.
                    "solution_incomplete": sum(
                        s.get("solution_incomplete") or 0 for s in sorted_reports
                    ),
                    # Q6 Part A: aggregate completeness-gate telemetry.
                    "completeness_incomplete": sum(
                        1 for s in sorted_reports if s.get("completeness_incomplete")
                    ),
                    "completeness_gap_total": sum(
                        s.get("completeness_gap") or 0 for s in sorted_reports
                    ),
                    "worker_version": "v3",
                })

    # Cross-section dedup pass — DISABLED per user directive ("pure OCR — throw
    # even if same"). If Gemini transcribes the same text for two different
    # sections (e.g. a reprinted exercise, or overlapping page slices), we
    # keep BOTH. Dedup was masking real OCR/schema issues by silently
    # dropping rows — better to surface duplicates so the user can see them
    # and decide what to do per-section.
    dedup_stats = None

    # Auto-link example touchpoints into the parent theory blocks. Best-
    # effort — failure here doesn't fail the bank.
    try:
        from app.services.example_linker import link_examples_to_theory_sync
        with SyncSession() as session:
            link_examples_to_theory_sync(session, book_id)
    except Exception as e:
        logger.warning("example_linker failed (book=%s): %s", book_id, e)

    # Mark the bank as ready / partial BEFORE the figure embedder runs.
    # The embedder's question-loading filter only loads from banks in
    # ("ready", "partial"). If we update bank status AFTER the embedder,
    # the embedder sees status="extracting" → loads 0 questions →
    # every question-context figure ends up "unattached", even when
    # the questions exist in the DB. Update first, embed second.
    bank_status = "ready" if counts["failed"] == 0 else "partial"
    with SyncSession() as session:
        _update_bank(session, bank_id, status=bank_status)

    # ─── Q-1: figure-extractor witness retry ──────────────────────────
    # The figure extractor's regen_meta.question_no field captures which
    # question_numbers are PRINTED on each page (next to figures). The
    # question extractor sometimes silently drops questions that contain
    # inline figures (the figure region disrupts the OCR text flow → the
    # question stem gets skipped). Reproducibly observed: MHT-CET 2024
    # (Q8, Q37, Q77, Q92 missing — all figure-bearing), Chemistry Organic
    # (15 question figures unattached because parent questions absent).
    #
    # Use the figure extractor's question_no list as the source-of-truth
    # witness. If any figure says question_no=N but no question with
    # question_number=N exists in DB, retry that section with a focused
    # prompt naming the missing N. Multi-column safe: retry uses the
    # same _Unit shape (section_start_heading preserved) the original
    # extraction used.
    try:
        await _retry_missing_questions_from_figures(
            book_id=book_id, bank_id=bank_id,
            units=units, pdf_bytes=pdf_bytes,
            system_prompt=system_prompt,
        )
    except Exception as e:
        logger.warning(
            "Q-1 figure-witness retry failed (book=%s): %s", book_id, e
        )

    # ─── Q-2: solution-completeness retry ─────────────────────────────
    # If Gemini emitted has_solution=true but the solution_text is
    # empty (observed across multiple books: Class 9th Maths 3/22 empty,
    # Geometry 7abc7ae1 7/21 empty), re-call Gemini for that specific
    # section listing the question_numbers whose solutions are missing,
    # and UPDATE the existing rows. Companion to the INTERNAL
    # CONSISTENCY prompt rule (commit 2077893) — that rule is the
    # front-line cure; this is the safety net for when Gemini ignores
    # the rule.
    try:
        await _retry_missing_solutions(
            book_id=book_id, bank_id=bank_id,
            units=units, pdf_bytes=pdf_bytes,
            system_prompt=system_prompt,
        )
    except Exception as e:
        logger.warning(
            "Q-2 solution-completeness retry failed (book=%s): %s", book_id, e
        )

    # ─── Q-3: Page-by-page undercount fallback (Task 1 Pass 3) ────────
    # Last-resort recovery for any unit that's STILL below
    # COMPLETENESS_THRESHOLD after Q-1 + Q-2. Scans one page at a time
    # with a focused prompt naming the q_nos already extracted so Gemini
    # only returns the missing ones. Bounded by per-book + per-unit caps
    # (see constants in _retry_undercount_page_by_page). Closes the
    # "Gemini undercounts on long Cat A sections even after targeted
    # retries" gap (observed in Integrals 4.x sections).
    try:
        await _retry_undercount_page_by_page(
            book_id=book_id, bank_id=bank_id,
            units=units, pdf_bytes=pdf_bytes,
            system_prompt=system_prompt,
        )
    except Exception as e:
        logger.warning(
            "Q-3 page-by-page undercount retry failed (book=%s): %s",
            book_id, e,
        )

    # Auto-embed figures now that questions exist. If figures were extracted
    # before questions, this is when question-tagged figure_references finally
    # land on the right questions. No-op if no figures yet — embedder is
    # idempotent.
    try:
        from app.services.figure_embedder import embed_figures_for_book_sync
        with SyncSession() as session:
            embed_counters = embed_figures_for_book_sync(session, book_id)
            logger.info(
                "[embed] post-questions book=%s %s", book_id, embed_counters
            )
    except Exception as e:
        logger.warning(
            "figure_embedder failed post-questions (book=%s): %s", book_id, e
        )

    # Final job status (bank status already set above)
    with SyncSession() as session:
        dropped = dedup_stats["dropped"] if dedup_stats else 0
        dedup_note = f" (deduped {dropped})" if dropped else ""
        msg = (
            f"Extracted {extracted_total} of {expected_total or '?'} expected "
            f"across {total} section(s){dedup_note} — "
            f"{counts['complete']} complete, {counts['partial']} partial, "
            f"{counts['empty']} empty, {counts['failed']} failed"
        )
        _update_job(
            session, job_id,
            status="succeeded",
            progress=100,
            message=msg,
            finished_at=datetime.utcnow(),
        )

    return {
        "ok": True,
        "sections_processed": total,
        "expected_total": expected_total,
        "extracted_total": extracted_total,
        **counts,
    }


def _extract_questions_v3(book_id: str, bank_id: str, job_id: str) -> dict[str, Any]:
    """Sync entrypoint registered with the dispatch table."""
    book_uuid = UUID(book_id)
    bank_uuid = UUID(bank_id)
    job_uuid = UUID(job_id)
    # NOTE: questions_status="running" is now written atomically by
    # the orchestrator's _dispatch_questions (via CAS) BEFORE this
    # worker fires. We no longer set it here — that was the source of
    # the read-then-write race that produced duplicate workers when a
    # coordinator ran between dispatch and worker pickup.
    try:
        result = asyncio.run(_run_v3(book_uuid, bank_uuid, job_uuid))
        # Derive terminal questions_status from bank outcome. The bank
        # has the authoritative per-section accounting; the book-level
        # field just summarises it. CAS-protected so a duplicate or
        # /re-extract reset can't be clobbered by our late write.
        with SyncSession() as session:
            b = session.get(Book, book_uuid)
            bk = session.get(QuestionBank, bank_uuid)
            if b is not None:
                if bk is None or bk.status == "failed":
                    new_status = "failed"
                elif bk.status == "partial":
                    new_status = "partial"
                else:  # "ready" or anything else clean
                    new_status = "done"
                from app.workers.orchestrator import cas_set_stage
                if cas_set_stage(
                    session, book_uuid, "questions", new_status,
                    from_states=("running",),
                ):
                    session.refresh(b)
                    from app.services.book_status import derive_book_status
                    derived = derive_book_status(b)
                    b.status = "extracting" if derived == "queued" else derived
                    session.commit()
                else:
                    logger.info(
                        "extract_questions_v3: dropping terminal write — "
                        "questions_status no longer 'running' book=%s",
                        book_uuid,
                    )

        # ORCH Day 5 — step the state machine forward. Coordinator
        # checks whether figures is also done and finalizes the book,
        # OR no-ops if figures still running. Idempotent.
        try:
            from app.workers.runner import dispatch
            dispatch("coordinate_extraction", str(book_uuid))
        except Exception as e:
            logger.warning(
                "extract_questions_v3: coordinator dispatch failed "
                "(continuing): %s", e,
            )
        return result
    except Exception as e:
        logger.exception("extract_questions_v3 crashed")
        with SyncSession() as session:
            _update_job(
                session, job_uuid,
                status="failed",
                error=str(e)[:2000],
                finished_at=datetime.utcnow(),
            )
            _update_bank(session, bank_uuid, status="failed",
                         last_error=str(e)[:2000])
            # CAS-protected failure write — drop if a sibling or reset
            # already moved questions_status out of "running".
            b = session.get(Book, book_uuid)
            if b is not None:
                from app.workers.orchestrator import cas_set_stage
                if cas_set_stage(
                    session, book_uuid, "questions", "failed",
                    from_states=("running",),
                ):
                    session.commit()
        # ORCH Day 5 — fire coordinator even on failure so it can
        # decide to retry (Day 7) or finalize the book as partial/failed.
        try:
            from app.workers.runner import dispatch
            dispatch("coordinate_extraction", str(book_uuid))
        except Exception as e2:
            logger.warning(
                "extract_questions_v3: coordinator dispatch (on-failure) "
                "failed: %s", e2,
            )
        return {"ok": False, "error": str(e)}


# Celery-mode task wrapper. Mirrors the pattern used in extract.py: the
# inline path uses _extract_questions_v3 directly via register_task; the
# Celery path uses this wrapper (Celery binds `self` as first arg). Both
# delegate to the same underlying function so behavior is identical.
@celery_app.task(name="extract_questions_v3", bind=True)
def extract_questions_v3_task(self, book_id: str, bank_id: str, job_id: str) -> dict[str, Any]:
    return _extract_questions_v3(book_id, bank_id, job_id)


register_task("extract_questions_v3", _extract_questions_v3)


# ---------------------------------------------------------------------------
# Per-section retry — re-runs _extract_unit for one section_ref and updates
# the matching entry in extraction_stats. Used by the diagnostic UI's
# "Retry section" button on partial / failed sections.
# ---------------------------------------------------------------------------
async def _run_section_retry(
    bank_id: UUID, section_ref: str, job_id: UUID
) -> dict[str, Any]:
    with SyncSession() as session:
        bank = session.get(QuestionBank, bank_id)
        if bank is None:
            raise ValueError(f"Bank {bank_id} not found")
        book = session.get(Book, bank.book_id)
        if book is None or not book.schema:
            raise ValueError("Book or schema missing")

        schema = BookSchema(**book.schema)
        is_multi_column = bool((book.analyser or {}).get("is_multi_column", False))
        units = _flatten_sections(schema, is_multi_column=is_multi_column)
        unit = next((u for u in units if u.id == section_ref), None)
        if unit is None:
            raise ValueError(f"Section {section_ref!r} not in schema")

        pdf_bytes = download_pdf(book.pdf_url or "")
        system_prompt = load_raw("question_extractor_v3")
        existing_stats = dict(bank.extraction_stats or {})
        # Capture primitive values BEFORE the session closes — accessing
        # `book.id` later raises DetachedInstanceError.
        book_id_local = book.id

        _update_job(session, job_id, status="running", progress=10,
                    message=f"Retrying {unit.title}")

    result = await _extract_unit(unit, pdf_bytes, system_prompt)
    kept = len(result.get("extracted") or [])
    identified = int(result.get("identified_total") or 0)
    status = _classify_unit(unit.expected, kept, identified, bool(result.get("ok")))

    with SyncSession() as session:
        _persist_unit(session, bank_id, book_id_local, unit, result)

        # Update the matching section entry in extraction_stats in place
        sections = list(existing_stats.get("sections") or [])
        new_section = {
            "section_ref": unit.id,
            "section_title": unit.title,
            "kind": unit.kind,
            "page_start": unit.page_start,
            "page_end": unit.page_end,
            "expected": unit.expected,
            "identified": identified,
            "extracted": kept,
            "rejected": result.get("rejected_count", 0),
            "rejected_items": result.get("rejected") or [],
            "status": status,
            "attempts": result.get("attempts", 0),
            "error": result.get("error"),
        }
        replaced = False
        for i, s in enumerate(sections):
            if s.get("section_ref") == unit.id:
                sections[i] = new_section
                replaced = True
                break
        if not replaced:
            sections.append(new_section)

        # Recompute totals — same exclusion rule as the main worker:
        # non-question blocks (Crossword / Activity / etc.) do not
        # contribute to any headline metric (expected_total /
        # extracted_total / status counters).
        counts = {"complete": 0, "partial": 0, "empty": 0, "failed": 0}
        expected_total = 0
        extracted_total = 0
        for s in sections:
            if _is_intentional_non_question_block(s.get("section_title")):
                continue
            counts[s["status"]] = counts.get(s["status"], 0) + 1
            expected_total += int(s.get("expected") or 0)
            extracted_total += int(s.get("extracted") or 0)

        legacy_blocks = [
            {
                "excluded_block_index": idx,
                "title": s["section_title"],
                "section_ref": s["section_ref"],
                "page_start": s["page_start"],
                "page_end": s["page_end"],
                "identified": s["identified"],
                "extracted": s["extracted"],
                "missed": (
                    0
                    if _is_intentional_non_question_block(s.get("section_title"))
                    else max(
                        0,
                        (s.get("expected") or s["identified"]) - s["extracted"],
                    )
                ),
                "status": _legacy_block_status(s["status"]),
            }
            for idx, s in enumerate(sections)
        ]
        existing_stats.update({
            "sections": sections,
            "totals": {
                "expected_total": expected_total,
                "extracted_total": extracted_total,
                **counts,
            },
            "blocks": legacy_blocks,
            "total_identified": sum(s["identified"] for s in sections),
            "total_extracted": extracted_total,
            "missed": sum(b["missed"] for b in legacy_blocks),
            "worker_version": "v3",
        })

        bank = session.get(QuestionBank, bank_id)
        if bank is not None:
            bank.extraction_stats = existing_stats
            # Bank stays "ready" unless a section is now failed
            bank.status = "partial" if counts["failed"] > 0 else "ready"
            session.commit()

        _update_job(
            session, job_id,
            status="succeeded",
            progress=100,
            message=f"Retried {unit.title}: {kept} extracted ({status})",
            finished_at=datetime.utcnow(),
        )

    # Re-run figure embedder so figures with question_no pointing at
    # questions in this section get attached now that the questions
    # exist (or got updated). Best-effort — retry already succeeded.
    try:
        from app.services.figure_embedder import embed_figures_for_book_sync
        with SyncSession() as own:
            # bank.book_id is needed; pull from the bank row
            from app.models.question_bank import QuestionBank as _QB
            qb = own.get(_QB, bank_id)
            if qb is not None and qb.book_id is not None:
                embed_figures_for_book_sync(own, qb.book_id)
    except Exception as e:
        logger.warning(
            "figure_embedder failed after question section retry "
            "(section=%s): %s", section_ref, e,
        )

    return {"ok": True, "section_ref": section_ref, "status": status,
            "extracted": kept, "identified": identified}


def _re_extract_section_v3(bank_id: str, section_ref: str, job_id: str) -> dict[str, Any]:
    bank_uuid = UUID(bank_id)
    job_uuid = UUID(job_id)
    try:
        return asyncio.run(_run_section_retry(bank_uuid, section_ref, job_uuid))
    except Exception as e:
        logger.exception("re_extract_section_v3 crashed")
        with SyncSession() as session:
            _update_job(
                session, job_uuid,
                status="failed",
                error=str(e)[:2000],
                finished_at=datetime.utcnow(),
            )
        return {"ok": False, "error": str(e)}


@celery_app.task(name="re_extract_section_v3", bind=True)
def re_extract_section_v3_task(self, bank_id: str, section_ref: str, job_id: str) -> dict[str, Any]:
    return _re_extract_section_v3(bank_id, section_ref, job_id)


register_task("re_extract_section_v3", _re_extract_section_v3)
