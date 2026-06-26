"""Stage 2 question extraction worker — excluded-block-driven.

Task names:
  - ``extract_questions_v2``   : full-bank extraction (iterate every excluded block)
  - ``re_extract_block``       : single-block retry, replaces that block's rows in-place

Key differences from the older ``extract_questions`` task:
  * Scope is ONE excluded block per Gemini call (tight, exhaustive, low-miss)
  * ``section_ref`` on each Question row comes from the linking cascade, not the page's theory section
  * Prompt returns rich OCR metadata (question_number, solution, etc.) — persisted to new columns
  * Self-check: ``identified_total`` must equal ``len(extracted)`` — one retry if mismatch
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.storage import download_pdf
from app.models.book import Book
from app.models.job import Job
from app.models.question import Question
from app.models.question_bank import QuestionBank
from app.services.prompt_loader import load_raw
from app.services.section_identity import resolve_section_uuid
from app.services.questions.linking import (
    LinkResult,
    SchemaIndex,
    resolve_block_link,
)
from app.utils.json_parse import parse_json
from app.workers.celery_app import celery_app
from app.workers.runner import register as register_task

logger = logging.getLogger(__name__)

_sync_engine = create_engine(settings.SYNC_DATABASE_URL, pool_pre_ping=True)
SyncSession = sessionmaker(bind=_sync_engine, class_=Session, autoflush=False)

GEMINI_MODEL = "gemini-2.5-flash"
MAX_ATTEMPTS = 2
# Generous output budget — large exercise blocks with solutions easily exceed 32k.
MAX_OUTPUT_TOKENS = 65536
# Hard timeout per Gemini call. Flash usually responds in 15-60s on a single
# block; anything past 150s is a hung request we want to abandon and retry.
GEMINI_TIMEOUT_S = 150
# Concurrent block extractions. Keeps Gemini happy (rate limits) while cutting
# wall-clock by ~3x for a typical 15-block chapter.
BLOCK_CONCURRENCY = 3
# Defensive ceiling for one block (Gemini timeout × MAX_ATTEMPTS + buffer).
# If a worker thread somehow exceeds this we abandon that block and continue.
BLOCK_TIMEOUT_S = GEMINI_TIMEOUT_S * MAX_ATTEMPTS + 60


# ---------------------------------------------------------------------------
# Tolerant JSON — Gemini sometimes emits invalid backslash escapes (e.g. LaTeX
# `\$` or `\,`) and occasionally truncates the response mid-array. We:
#   1. Escape any backslash NOT followed by a valid JSON escape char.
#   2. If parse still fails, salvage by closing the array at the last complete
#      object so we lose only the truncated tail, not the whole batch.
# ---------------------------------------------------------------------------
_VALID_ESC = set('"\\/bfnrtu')


def _fix_bad_escapes(s: str) -> str:
    out: list[str] = []
    in_str = False
    i = 0
    while i < len(s):
        ch = s[i]
        if not in_str:
            if ch == '"':
                in_str = True
            out.append(ch)
            i += 1
            continue
        # inside a string
        if ch == '"':
            in_str = False
            out.append(ch)
            i += 1
            continue
        if ch == "\\":
            nxt = s[i + 1] if i + 1 < len(s) else ""
            if nxt in _VALID_ESC:
                out.append(ch)
                out.append(nxt)
                i += 2
            else:
                # stray backslash → double it so JSON accepts it
                out.append("\\\\")
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _tolerant_parse(raw: str) -> Any:
    """Parse Gemini JSON tolerantly. Order:
    1. Standard parse_json (strips fences, removes trailing commas).
    2. Re-parse after fixing bad backslash escapes.
    3. Salvage partial array if the response was truncated.
    """
    try:
        return parse_json(raw)
    except (ValueError, json.JSONDecodeError):
        pass
    try:
        fixed = _fix_bad_escapes(raw)
        return parse_json(fixed)
    except (ValueError, json.JSONDecodeError):
        pass
    salvaged = _salvage_truncated_extracted(raw)
    if salvaged is not None:
        return salvaged
    raise ValueError("Unrecoverable JSON")


def _salvage_truncated_extracted(raw: str) -> dict[str, Any] | None:
    """Last-resort parser: if the top-level JSON is truncated mid-array,
    return ``{identified_total, extracted}`` using only the objects we can
    fully parse from the ``extracted`` array.
    """
    m = re.search(r'"extracted"\s*:\s*\[', raw)
    if not m:
        return None
    arr_start = m.end()
    items: list[dict[str, Any]] = []
    depth = 0
    in_str = False
    esc = False
    obj_start: int | None = None
    i = arr_start
    while i < len(raw):
        ch = raw[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                if depth == 0:
                    obj_start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and obj_start is not None:
                    chunk = raw[obj_start : i + 1]
                    try:
                        items.append(json.loads(_fix_bad_escapes(chunk)))
                    except json.JSONDecodeError:
                        pass
                    obj_start = None
            elif ch == "]" and depth == 0:
                break
        i += 1
    if not items:
        return None
    # Try to recover identified_total; default to len(items).
    idm = re.search(r'"identified_total"\s*:\s*(\d+)', raw)
    identified = int(idm.group(1)) if idm else len(items)
    return {"identified_total": identified, "extracted": items}

# Theory-section inline extraction — reserve a high-numbered index range so the
# integer block_idx column stays unique across "excluded" and "inline" sources.
INLINE_BLOCK_BASE = 10000
# Pass 3 exhaustive sweep — reserved range above INLINE so it never collides.
SWEEP_BLOCK_BASE = 20000

# Content-type tags (from analyser) that hint a theory section likely contains
# question-like items worth a Gemini call. Matched case-insensitively.
QUESTION_LIKE_TAGS = frozenset(
    {
        "example", "examples",
        "worked_example", "worked_examples", "illustration", "illustrations",
        "problem", "problems",
        "exercise", "exercises",
        "in_text_prompt", "check_your_understanding", "try_it",
        "review", "review_questions",
        "questions",
    }
)

ALLOWED_KINDS = {"exercise", "example", "problem", "try_it", "review", "mcq", "other"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _update_job(session: Session, job_id: UUID, **fields) -> None:
    job = session.get(Job, job_id)
    if job is None:
        return
    for k, v in fields.items():
        setattr(job, k, v)
    from datetime import datetime, timezone
    job.last_heartbeat_at = datetime.now(timezone.utc)
    session.commit()


from app.core.heartbeat import Heartbeat as _Heartbeat, NullHeartbeat as _NullCtx  # noqa: E402


def _slice_pdf(pdf_bytes: bytes, page_start: int | None, page_end: int | None) -> bytes:
    """Return a PDF containing only pages page_start..page_end (1-indexed)."""
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
        logger.warning("PDF slice failed (%s-%s): %s", page_start, page_end, e)
        return pdf_bytes


def _call_gemini_sync(pdf_slice: bytes, system_prompt: str, user_prompt: str) -> str:
    """Upload a single PDF slice to Gemini, return raw JSON text.

    Real socket timeout via ``HttpOptions(timeout=...)`` — when the call
    exceeds the budget the SDK raises and we propagate. No more abandoned
    daemon threads holding open sockets.
    """
    from app.core.gemini_runtime import call_gemini_with_pdf

    return call_gemini_with_pdf(
        pdf_bytes=pdf_slice,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        model=GEMINI_MODEL,
        timeout_s=GEMINI_TIMEOUT_S,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        temperature=0.0,
        display_name="excluded_block.pdf",
    )


def _build_user_prompt(
    *,
    title: str,
    page_start: int | None,
    page_end: int | None,
    link_section_ref: str | None,
    retry_note: str | None = None,
    custom_instructions: str | None = None,
) -> str:
    header = ""
    if custom_instructions:
        header += (
            "[HIGHEST PRIORITY OVERRIDE — these instructions take precedence "
            "over every default rule below. Follow them exactly while still "
            "honouring the verbatim/no-invention guarantees.]\n"
            f"{custom_instructions.strip()}\n\n"
        )
    header += (
        f'Section scope: "{title}" — pages {page_start}-{page_end}.\n'
        f'This block is linked to theory section "{link_section_ref or "(unlinked)"}".\n\n'
        "Go page by page. Transcribe EVERY printed question verbatim.\n"
        "Return strict JSON matching the output format.\n"
    )
    if retry_note:
        header += (
            "\nPREVIOUS ATTEMPT WAS INCOMPLETE: "
            + retry_note
            + " — re-read every page and include any you missed.\n"
        )
    return header


async def _call_with_retry(
    *,
    pdf_slice: bytes,
    title: str,
    page_start: int | None,
    page_end: int | None,
    link_section_ref: str | None,
    job_uuid: UUID | None = None,
    progress: int = 0,
    custom_instructions: str | None = None,
) -> tuple[dict[str, Any], int, list[str]]:
    """Call Gemini; if identified_total != len(extracted), retry once with a nudge.

    Emits a heartbeat to the job row every 10s while the Gemini call is
    in-flight so the UI shows "still working — Ns elapsed" instead of looking
    hung. Also retries once on TimeoutError.
    """
    system_prompt = load_raw("question_extractor")
    failures: list[str] = []
    retry_note: str | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        user_prompt = _build_user_prompt(
            title=title,
            page_start=page_start,
            page_end=page_end,
            link_section_ref=link_section_ref,
            retry_note=retry_note,
            custom_instructions=custom_instructions,
        )
        try:
            heartbeat_ctx = (
                _Heartbeat(
                    job_uuid,
                    f"Extracting {title} (pages {page_start}-{page_end})",
                    progress,
                )
                if job_uuid is not None
                else _NullCtx()
            )
            with heartbeat_ctx:
                raw = await asyncio.to_thread(
                    _call_gemini_sync, pdf_slice, system_prompt, user_prompt
                )
            data = _tolerant_parse(raw)
            if not isinstance(data, dict):
                raise ValueError("response was not a JSON object")

            extracted = data.get("extracted")
            if extracted is None:
                # Back-compat: prior prompt used "questions"
                extracted = data.get("questions") or []
            if not isinstance(extracted, list):
                extracted = []

            identified = data.get("identified_total")
            try:
                identified_total = int(identified) if identified is not None else len(extracted)
            except (TypeError, ValueError):
                identified_total = len(extracted)

            if identified_total != len(extracted) and attempt < MAX_ATTEMPTS:
                retry_note = (
                    f"identified_total={identified_total} but returned {len(extracted)} items"
                )
                failures.append(f"attempt {attempt}: {retry_note}")
                continue

            return (
                {"identified_total": identified_total, "extracted": extracted},
                attempt,
                failures,
            )

        except Exception as e:
            msg = f"attempt {attempt}: {e}"
            logger.warning("Question extraction error (%s): %s", title, msg)
            failures.append(msg[:300])

    return ({"identified_total": 0, "extracted": []}, MAX_ATTEMPTS, failures)


def _coerce_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "1")
    return bool(v)


def _coerce_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _coerce_str(v: Any, max_len: int | None = None) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    if max_len is not None:
        s = s[:max_len]
    return s


# ---------------------------------------------------------------------------
# Target discovery — unified list of (excluded_blocks + inline theory sections)
# ---------------------------------------------------------------------------
def _collect_inline_targets(schema_dict: dict) -> list[dict[str, Any]]:
    """Walk schema.sections[] and collect theory sections whose content_types
    intersect QUESTION_LIKE_TAGS (analyser-tagged examples/problems/try-its).
    """
    out: list[dict[str, Any]] = []

    def walk(arr: Any) -> None:
        for s in arr or []:
            if not isinstance(s, dict):
                continue
            if s.get("type") == "excluded":
                continue
            ct = {str(x).strip().lower() for x in (s.get("content_types") or [])}
            if ct & QUESTION_LIKE_TAGS:
                out.append(
                    {
                        "section_ref": str(s.get("id") or "").strip() or None,
                        "title": str(s.get("title") or ""),
                        "page_start": _coerce_int(s.get("page_start")),
                        "page_end": _coerce_int(s.get("page_end")),
                        "content_types": sorted(ct),
                    }
                )
            walk(s.get("subsections") or [])

    walk(schema_dict.get("sections") or [])
    return out


def _collect_sweep_targets(
    schema_dict: dict,
    covered_section_refs: set[str],
) -> list[dict[str, Any]]:
    """Walk schema.sections[] and collect EVERY leaf theory section (no
    subsections, not type=excluded) whose section_ref is NOT already covered
    by the excluded-blocks or inline-tagged passes.

    Pass 3 uses this list so books whose analyser missed a `question_like`
    tag — or whose questions are sprinkled under plain theory headings — are
    still scanned exhaustively. Dedupe against existing rows prevents
    duplicates.
    """
    out: list[dict[str, Any]] = []

    def walk(arr: Any) -> None:
        for s in arr or []:
            if not isinstance(s, dict):
                continue
            if s.get("type") == "excluded":
                continue
            children = s.get("subsections") or []
            if children:
                walk(children)
                continue
            # Leaf section
            section_ref = str(s.get("id") or "").strip() or None
            if not section_ref or section_ref in covered_section_refs:
                continue
            page_start = _coerce_int(s.get("page_start"))
            page_end = _coerce_int(s.get("page_end"))
            if page_start is None:
                continue
            out.append(
                {
                    "section_ref": section_ref,
                    "title": str(s.get("title") or ""),
                    "page_start": page_start,
                    "page_end": page_end,
                    "content_types": sorted(
                        {str(x).strip().lower() for x in (s.get("content_types") or [])}
                    ),
                }
            )

    walk(schema_dict.get("sections") or [])
    return out


def _normalise_kind(raw_kind: Any, raw_evidence: Any, source_kind_default: str) -> str:
    """Accept only anchored kinds; require non-empty kind_evidence for non-default,
    else fall back to the source default (``exercise`` for excluded blocks).

    Rule: if Gemini didn't point to a printed label (kind_evidence empty), we
    trust only the source default — never invent a label.
    """
    k = _coerce_str(raw_kind)
    if not k:
        return source_kind_default
    k = k.lower()
    if k not in ALLOWED_KINDS:
        return source_kind_default
    evidence = _coerce_str(raw_evidence)
    if k != source_kind_default and k != "other" and not evidence:
        # No printed anchor → demote to default kind for this source.
        return source_kind_default
    return k


def _existing_question_keys(
    session: Session, bank_id: UUID, regen_id: UUID | None = None
) -> set[tuple[int | None, str]]:
    """Return a set of (page, raw_text[:120]) keys already in the bank for
    the given regen scope. ``regen_id=None`` matches originals (regen_id IS NULL).
    """
    stmt = select(Question.page_start, Question.raw_text).where(
        Question.bank_id == bank_id
    )
    if regen_id is None:
        stmt = stmt.where(Question.regen_id.is_(None))
    else:
        stmt = stmt.where(Question.regen_id == regen_id)
    rows = session.execute(stmt).all()
    return {(r[0], (r[1] or "")[:120]) for r in rows}


# ---------------------------------------------------------------------------
# Core: extract ONE excluded block, persist its rows, return per-block stats
# ---------------------------------------------------------------------------
def _extract_one_block(
    *,
    session: Session,
    bank: QuestionBank,
    book: Book,
    pdf_bytes: bytes,
    index: SchemaIndex,
    block_idx: int,
    ex: dict,
    job_uuid: UUID | None = None,
    progress: int = 0,
    regen_id: UUID | None = None,
    custom_instructions: str | None = None,
) -> dict[str, Any]:
    title = str(ex.get("title") or f"Excluded #{block_idx}")
    page_start = _coerce_int(ex.get("page_start"))
    page_end = _coerce_int(ex.get("page_end"))

    link = resolve_block_link(
        title=title,
        page_start=page_start,
        page_end=page_end,
        index=index,
    )
    section_ref = link.section_ref
    section_title = None
    if section_ref:
        match = next((s for s in index.sections if s.id == section_ref), None)
        if match:
            section_title = match.title

    pdf_slice = _slice_pdf(pdf_bytes, page_start, page_end)

    payload, attempts, failures = asyncio.run(
        _call_with_retry(
            pdf_slice=pdf_slice,
            title=title,
            page_start=page_start,
            page_end=page_end,
            link_section_ref=section_ref,
            job_uuid=job_uuid,
            progress=progress,
            custom_instructions=custom_instructions,
        )
    )

    extracted = list(payload.get("extracted") or [])
    identified_total = int(payload.get("identified_total") or 0)

    # Replace rows for this block within the current scope (regen vs original).
    delete_stmt = delete(Question).where(
        Question.bank_id == bank.id,
        Question.excluded_block_index == block_idx,
    )
    if regen_id is None:
        delete_stmt = delete_stmt.where(Question.regen_id.is_(None))
    else:
        delete_stmt = delete_stmt.where(Question.regen_id == regen_id)
    session.execute(delete_stmt)

    inserted = 0
    for item in extracted:
        if not isinstance(item, dict):
            continue
        raw_text = _coerce_str(item.get("raw_text"))
        if not raw_text:
            continue
        page_num = _coerce_int(item.get("page")) or page_start

        solution_text = _coerce_str(item.get("solution"))
        has_solution = _coerce_bool(item.get("has_solution")) or bool(solution_text)
        kind = _normalise_kind(
            item.get("kind"),
            item.get("kind_evidence"),
            source_kind_default="exercise",
        )

        row = Question(
            bank_id=bank.id,
            book_id=book.id,
            section_ref=section_ref,
            section_uuid=resolve_section_uuid(session, book.id, section_ref),
            section_title=section_title,
            page_start=page_num,
            page_end=page_num,
            raw_text=raw_text,
            qc_local={
                "pass": True,
                "score": 1.0,
                "failures": failures,
            },
            attempts=attempts,
            status="passed",
            # Phase 1 linking
            excluded_block_ref=title,
            excluded_block_index=block_idx,
            excluded_block_page_start=page_start,
            excluded_block_page_end=page_end,
            link_method=link.method,
            link_confidence=link.confidence,
            link_rule_trace=link.trace,
            linked_by="system",
            linked_at=datetime.utcnow(),
            # Stage 2 OCR metadata
            question_number=_coerce_str(item.get("question_number"), 32),
            exercise_ref=_coerce_str(item.get("exercise_ref"), 128),
            chapter_ref=_coerce_str(item.get("chapter_ref"), 64),
            sub_part=_coerce_str(item.get("sub_part"), 8),
            question_type=_coerce_str(item.get("question_type"), 32),
            has_options=_coerce_bool(item.get("has_options")),
            solution_text=solution_text,
            has_solution=has_solution,
            identified_total=identified_total,
            kind=kind,
            regen_id=regen_id,
        )
        session.add(row)
        inserted += 1

    session.commit()

    block_stats: dict[str, Any] = {
        "excluded_block_index": block_idx,
        "title": title,
        "page_start": page_start,
        "page_end": page_end,
        "section_ref": section_ref,
        "link_method": link.method,
        "link_confidence": link.confidence,
        "identified": identified_total,
        "extracted": inserted,
        "attempts": attempts,
        "missed": max(0, identified_total - inserted),
        "status": (
            "ok" if inserted and inserted == identified_total
            else "partial" if inserted and inserted < identified_total
            else "empty" if identified_total == 0
            else "failed"
        ),
        "failures": failures,
    }
    return block_stats


# ---------------------------------------------------------------------------
# Core: extract ONE theory-section inline target (Examples / Problems / Try-It
# scattered inside the theory body), persist its rows with dedupe.
# ---------------------------------------------------------------------------
def _extract_one_inline(
    *,
    session: Session,
    bank: QuestionBank,
    book: Book,
    pdf_bytes: bytes,
    inline_idx: int,
    target: dict[str, Any],
    existing_keys: set[tuple[int | None, str]],
    job_uuid: UUID | None = None,
    progress: int = 0,
    regen_id: UUID | None = None,
    custom_instructions: str | None = None,
) -> dict[str, Any]:
    """Scan a theory section's pages for in-text question-like items. Unlike
    excluded blocks, ``section_ref`` is known directly (no cascade needed) and
    new inserts are deduped against already-extracted rows in this bank.
    """
    block_idx = INLINE_BLOCK_BASE + inline_idx
    section_ref = target.get("section_ref")
    title = f"Inline · §{section_ref or '?'} {target.get('title') or ''}".strip()
    page_start = _coerce_int(target.get("page_start"))
    page_end = _coerce_int(target.get("page_end"))

    pdf_slice = _slice_pdf(pdf_bytes, page_start, page_end)

    payload, attempts, failures = asyncio.run(
        _call_with_retry(
            pdf_slice=pdf_slice,
            title=title,
            page_start=page_start,
            page_end=page_end,
            link_section_ref=section_ref,
            job_uuid=job_uuid,
            progress=progress,
            custom_instructions=custom_instructions,
        )
    )

    extracted = list(payload.get("extracted") or [])
    identified_total = int(payload.get("identified_total") or 0)

    # Replace only this inline block's rows within the current scope.
    delete_stmt = delete(Question).where(
        Question.bank_id == bank.id,
        Question.excluded_block_index == block_idx,
    )
    if regen_id is None:
        delete_stmt = delete_stmt.where(Question.regen_id.is_(None))
    else:
        delete_stmt = delete_stmt.where(Question.regen_id == regen_id)
    session.execute(delete_stmt)

    inserted = 0
    skipped_dup = 0
    for item in extracted:
        if not isinstance(item, dict):
            continue
        raw_text = _coerce_str(item.get("raw_text"))
        if not raw_text:
            continue
        page_num = _coerce_int(item.get("page")) or page_start

        # Dedupe: skip if a question with same (page, raw_text[:120]) already
        # exists in this bank (e.g. same Example reprinted in an exercise block).
        key = (page_num, raw_text[:120])
        if key in existing_keys:
            skipped_dup += 1
            continue
        existing_keys.add(key)

        solution_text = _coerce_str(item.get("solution"))
        has_solution = _coerce_bool(item.get("has_solution")) or bool(solution_text)
        # Default kind for theory-inline sources is "other" — we only promote to
        # example/problem/try_it when Gemini supplies anchored evidence.
        kind = _normalise_kind(
            item.get("kind"),
            item.get("kind_evidence"),
            source_kind_default="other",
        )

        row = Question(
            bank_id=bank.id,
            book_id=book.id,
            section_ref=section_ref,
            section_uuid=resolve_section_uuid(session, book.id, section_ref),
            section_title=target.get("title"),
            page_start=page_num,
            page_end=page_num,
            raw_text=raw_text,
            qc_local={"pass": True, "score": 1.0, "failures": failures},
            attempts=attempts,
            status="passed",
            # Synthetic linking for inline — direct section reference, no cascade.
            excluded_block_ref=title,
            excluded_block_index=block_idx,
            excluded_block_page_start=page_start,
            excluded_block_page_end=page_end,
            link_method="direct_section",
            link_confidence=1.0,
            link_rule_trace=[{"rule": "direct_section", "matched": section_ref}],
            linked_by="system",
            linked_at=datetime.utcnow(),
            # OCR metadata
            question_number=_coerce_str(item.get("question_number"), 32),
            exercise_ref=_coerce_str(item.get("exercise_ref"), 128),
            chapter_ref=_coerce_str(item.get("chapter_ref"), 64),
            sub_part=_coerce_str(item.get("sub_part"), 8),
            question_type=_coerce_str(item.get("question_type"), 32),
            has_options=_coerce_bool(item.get("has_options")),
            solution_text=solution_text,
            has_solution=has_solution,
            identified_total=identified_total,
            kind=kind,
            regen_id=regen_id,
        )
        session.add(row)
        inserted += 1

    session.commit()

    return {
        "excluded_block_index": block_idx,
        "title": title,
        "page_start": page_start,
        "page_end": page_end,
        "section_ref": section_ref,
        "link_method": "direct_section",
        "link_confidence": 1.0,
        "identified": identified_total,
        "extracted": inserted,
        "skipped_duplicates": skipped_dup,
        "attempts": attempts,
        "missed": max(0, identified_total - inserted - skipped_dup),
        "status": (
            "ok" if inserted and inserted + skipped_dup >= identified_total
            else "partial" if inserted and inserted < identified_total
            else "empty" if identified_total == 0
            else "failed"
        ),
        "source": "inline",
        "failures": failures,
    }


# ---------------------------------------------------------------------------
# Bank-wide stats rollup helpers
# ---------------------------------------------------------------------------
def _compute_bank_totals(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    total_identified = sum(int(b.get("identified") or 0) for b in blocks)
    total_extracted = sum(int(b.get("extracted") or 0) for b in blocks)
    partial_blocks = sum(1 for b in blocks if b.get("status") == "partial")
    failed_blocks = sum(1 for b in blocks if b.get("status") == "failed")
    return {
        "total_identified": total_identified,
        "total_extracted": total_extracted,
        "missed": max(0, total_identified - total_extracted),
        # Surfaced so the UI / job message can warn about silent partial
        # extractions (the previous behaviour was to roll them up under a
        # cheerful "Extracted N of M" message that hid the diff).
        "partial_blocks": partial_blocks,
        "failed_blocks": failed_blocks,
        "blocks": blocks,
    }


def _upsert_block_stats(
    existing: dict[str, Any] | None, new_block: dict[str, Any]
) -> dict[str, Any]:
    """Update or append a single block's stats inside extraction_stats.blocks."""
    stats = dict(existing or {})
    blocks = list(stats.get("blocks") or [])
    idx = new_block["excluded_block_index"]
    replaced = False
    for i, b in enumerate(blocks):
        if b.get("excluded_block_index") == idx:
            blocks[i] = new_block
            replaced = True
            break
    if not replaced:
        blocks.append(new_block)
    blocks.sort(key=lambda b: b.get("excluded_block_index") or 0)
    return _compute_bank_totals(blocks)


# ---------------------------------------------------------------------------
# Task: full-bank extraction
# ---------------------------------------------------------------------------
@celery_app.task(name="extract_questions_v2", bind=True)
def extract_questions_v2_task(self, book_id: str, job_id: str) -> dict:
    book_uuid = UUID(book_id)
    job_uuid = UUID(job_id)

    with SyncSession() as session:
        _update_job(
            session,
            job_uuid,
            status="running",
            started_at=datetime.utcnow(),
            message="Loading book + schema",
            progress=2,
        )

        book = session.get(Book, book_uuid)
        if book is None or not book.pdf_url:
            _update_job(
                session, job_uuid,
                status="failed",
                error="Book or PDF missing",
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "reason": "book_missing"}
        if not book.schema:
            _update_job(
                session, job_uuid,
                status="failed",
                error="Book schema missing — run /analyse first",
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "reason": "schema_missing"}

        bank = session.execute(
            select(QuestionBank)
            .where(QuestionBank.book_id == book_uuid)
            .where(QuestionBank.status.in_(["pending", "extracting"]))
            .order_by(QuestionBank.created_at.desc())
        ).scalars().first()
        if bank is None:
            _update_job(
                session, job_uuid,
                status="failed",
                error="No pending QuestionBank for this book",
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "reason": "no_bank"}

        bank.status = "extracting"
        bank.last_error = None
        session.commit()

        try:
            pdf_bytes = download_pdf(book.pdf_url)
            schema_dict = book.schema or {}
            index = SchemaIndex.from_schema(schema_dict)
            excluded = list(schema_dict.get("excluded_sections") or [])

            inline_targets = _collect_inline_targets(schema_dict)
            covered_refs: set[str] = {
                t["section_ref"] for t in inline_targets if t.get("section_ref")
            }
            sweep_targets = _collect_sweep_targets(schema_dict, covered_refs)
            total = len(excluded) + len(inline_targets) + len(sweep_targets)

            if total == 0:
                # Nothing to extract — no excluded blocks, no taggable inline
                # sections, no sweepable leaf sections (e.g. unanalysed book).
                bank.extraction_stats = {
                    "total_identified": 0,
                    "total_extracted": 0,
                    "missed": 0,
                    "blocks": [],
                }
                bank.status = "ready"
                session.commit()
                _update_job(
                    session, job_uuid,
                    status="succeeded",
                    progress=100,
                    message="No extractable sections in this book",
                    finished_at=datetime.utcnow(),
                )
                return {"ok": True, "bank_id": str(bank.id), "total_questions": 0}
            block_rows: list[dict[str, Any]] = []
            step = 0

            bank_id = bank.id
            book_id = book.id

            def _run_block_task(block_idx: int, ex: dict, progress_now: int) -> dict[str, Any]:
                """Run one excluded-block extraction in its own DB session so
                multiple blocks can persist concurrently without contending on
                the orchestrator session.
                """
                with SyncSession() as own:
                    own_bank = own.get(QuestionBank, bank_id)
                    own_book = own.get(Book, book_id)
                    if own_bank is None or own_book is None:
                        raise RuntimeError("bank/book vanished mid-extraction")
                    stats = _extract_one_block(
                        session=own,
                        bank=own_bank,
                        book=own_book,
                        pdf_bytes=pdf_bytes,
                        index=index,
                        block_idx=block_idx,
                        ex=ex,
                        job_uuid=job_uuid,
                        progress=progress_now,
                    )
                    stats["source"] = "excluded"
                    return stats

            def _run_inline_task(
                inline_idx: int,
                target: dict[str, Any],
                source_label: str,
                shared_keys: set[tuple[int | None, str]],
                progress_now: int,
            ) -> dict[str, Any]:
                """Run one inline / sweep section extraction in its own session.
                ``shared_keys`` is a thread-safe-ish dedupe set updated under a
                lock inside the worker.
                """
                with SyncSession() as own:
                    own_bank = own.get(QuestionBank, bank_id)
                    own_book = own.get(Book, book_id)
                    if own_bank is None or own_book is None:
                        raise RuntimeError("bank/book vanished mid-extraction")
                    stats = _extract_one_inline(
                        session=own,
                        bank=own_bank,
                        book=own_book,
                        pdf_bytes=pdf_bytes,
                        inline_idx=inline_idx,
                        target=target,
                        existing_keys=shared_keys,
                        job_uuid=job_uuid,
                        progress=progress_now,
                    )
                    stats["source"] = source_label
                    return stats

            # ---- Pass 1: excluded blocks ----------------------------------------
            valid_excluded: list[tuple[int, dict]] = [
                (i, ex) for i, ex in enumerate(excluded) if isinstance(ex, dict)
            ]
            base_progress_p1 = 10 + int(85 * step / max(total, 1))
            _update_job(
                session, job_uuid,
                message=f"Pass 1 — extracting {len(valid_excluded)} exercise blocks (×{BLOCK_CONCURRENCY} parallel)",
                progress=base_progress_p1,
            )
            with ThreadPoolExecutor(max_workers=BLOCK_CONCURRENCY) as pool:
                futures = {
                    pool.submit(_run_block_task, idx, ex, base_progress_p1): (idx, ex)
                    for idx, ex in valid_excluded
                }
                for fut in as_completed(futures):
                    try:
                        block_stats = fut.result(timeout=BLOCK_TIMEOUT_S)
                    except Exception:
                        logger.exception("Pass 1 block failed")
                        continue
                    block_rows.append(block_stats)
                    bank.extraction_stats = _compute_bank_totals(block_rows)
                    session.commit()
                    step += 1
                    _update_job(
                        session, job_uuid,
                        message=f"✓ {block_stats.get('title','?')[:40]} — {block_stats['extracted']}/{block_stats['identified']} ({step}/{total})",
                        progress=10 + int(85 * step / max(total, 1)),
                    )

            # ---- Pass 2: inline-tagged theory sections --------------------------
            # Dedupe against everything Pass 1 just persisted.
            existing_keys = _existing_question_keys(session, bank.id)
            base_progress_p2 = 10 + int(85 * step / max(total, 1))
            if inline_targets:
                _update_job(
                    session, job_uuid,
                    message=f"Pass 2 — scanning {len(inline_targets)} inline sections (×{BLOCK_CONCURRENCY})",
                    progress=base_progress_p2,
                )
                with ThreadPoolExecutor(max_workers=BLOCK_CONCURRENCY) as pool:
                    futures = {
                        pool.submit(
                            _run_inline_task,
                            idx, target, "inline", existing_keys, base_progress_p2,
                        ): (idx, target)
                        for idx, target in enumerate(inline_targets)
                    }
                    for fut in as_completed(futures):
                        try:
                            block_stats = fut.result()
                        except Exception:
                            logger.exception("Pass 2 inline failed")
                            continue
                        block_rows.append(block_stats)
                        bank.extraction_stats = _compute_bank_totals(block_rows)
                        session.commit()
                        step += 1
                        _update_job(
                            session, job_uuid,
                            message=f"✓ §{block_stats.get('section_ref') or '?'} — {block_stats['extracted']} new ({step}/{total})",
                            progress=10 + int(85 * step / max(total, 1)),
                        )

            # ---- Pass 3: exhaustive sweep of every other leaf section -----------
            # Refresh dedupe keys with everything Pass 1+2 added so far.
            existing_keys = _existing_question_keys(session, bank.id)
            base_progress_p3 = 10 + int(85 * step / max(total, 1))
            if sweep_targets:
                _update_job(
                    session, job_uuid,
                    message=f"Pass 3 — sweeping {len(sweep_targets)} leaf sections (×{BLOCK_CONCURRENCY})",
                    progress=base_progress_p3,
                )
                with ThreadPoolExecutor(max_workers=BLOCK_CONCURRENCY) as pool:
                    futures = {}
                    for sweep_idx, target in enumerate(sweep_targets):
                        inline_idx_for_sweep = (
                            SWEEP_BLOCK_BASE - INLINE_BLOCK_BASE + sweep_idx
                        )
                        fut = pool.submit(
                            _run_inline_task,
                            inline_idx_for_sweep, target, "sweep",
                            existing_keys, base_progress_p3,
                        )
                        futures[fut] = (sweep_idx, target)
                    for fut in as_completed(futures):
                        try:
                            block_stats = fut.result()
                        except Exception:
                            logger.exception("Pass 3 sweep failed")
                            continue
                        block_rows.append(block_stats)
                        bank.extraction_stats = _compute_bank_totals(block_rows)
                        session.commit()
                        step += 1
                        _update_job(
                            session, job_uuid,
                            message=f"✓ sweep §{block_stats.get('section_ref') or '?'} ({step}/{total})",
                            progress=10 + int(85 * step / max(total, 1)),
                        )

            bank.status = "ready"
            session.commit()

            # Auto-embed figures now that questions exist (v2 path).
            try:
                from app.services.figure_embedder import embed_figures_for_book_sync
                embed_counters = embed_figures_for_book_sync(session, bank.book_id)
                logger.info(
                    "[embed] post-questions-v2 book=%s %s",
                    bank.book_id, embed_counters,
                )
            except Exception as e:
                logger.warning(
                    "figure_embedder failed post-questions-v2 (book=%s): %s",
                    bank.book_id, e,
                )

            totals = bank.extraction_stats or {}
            partial_n = int(totals.get("partial_blocks", 0))
            failed_n = int(totals.get("failed_blocks", 0))
            extracted = totals.get("total_extracted", 0)
            identified = totals.get("total_identified", 0)
            base = f"Extracted {extracted} of {identified} identified across {total} blocks"
            if partial_n or failed_n:
                base += (
                    f" — {partial_n} block(s) partial, {failed_n} failed. "
                    "Click ↺ on each affected block to re-run."
                )
            _update_job(
                session, job_uuid,
                status="succeeded",
                progress=100,
                message=base,
                finished_at=datetime.utcnow(),
            )
            return {
                "ok": True,
                "bank_id": str(bank.id),
                "total_questions": totals.get("total_extracted", 0),
                "total_identified": totals.get("total_identified", 0),
            }

        except Exception as e:
            logger.exception("extract_questions_v2_task failed")
            session.rollback()
            bank = session.get(QuestionBank, bank.id)
            if bank is not None:
                bank.status = "failed"
                bank.last_error = str(e)[:2000]
                session.commit()
            _update_job(
                session, job_uuid,
                status="failed",
                error=str(e)[:2000],
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "error": str(e)}


def _extract_questions_v2(book_id: str, job_id: str) -> dict:
    return extract_questions_v2_task(None, book_id, job_id)  # type: ignore[arg-type]


register_task("extract_questions_v2", _extract_questions_v2)


# ---------------------------------------------------------------------------
# Task: per-block re-extract
# ---------------------------------------------------------------------------
@celery_app.task(name="re_extract_block", bind=True)
def re_extract_block_task(
    self, bank_id: str, block_idx: int, job_id: str
) -> dict:
    bank_uuid = UUID(bank_id)
    job_uuid = UUID(job_id)

    with SyncSession() as session:
        _update_job(
            session, job_uuid,
            status="running",
            started_at=datetime.utcnow(),
            message=f"Re-extracting block #{block_idx}",
            progress=5,
        )

        bank = session.get(QuestionBank, bank_uuid)
        if bank is None:
            _update_job(
                session, job_uuid,
                status="failed",
                error="QuestionBank not found",
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "reason": "bank_missing"}

        book = session.get(Book, bank.book_id)
        if book is None or not book.pdf_url or not book.schema:
            _update_job(
                session, job_uuid,
                status="failed",
                error="Book, PDF, or schema missing",
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "reason": "book_missing"}

        schema_dict = book.schema or {}
        excluded = list(schema_dict.get("excluded_sections") or [])
        inline_targets = _collect_inline_targets(schema_dict)

        is_inline = block_idx >= INLINE_BLOCK_BASE
        if is_inline:
            inline_idx = block_idx - INLINE_BLOCK_BASE
            if inline_idx < 0 or inline_idx >= len(inline_targets):
                _update_job(
                    session, job_uuid,
                    status="failed",
                    error=f"Inline block index {inline_idx} out of range (0..{len(inline_targets)-1})",
                    finished_at=datetime.utcnow(),
                )
                return {"ok": False, "reason": "inline_out_of_range"}
        elif block_idx < 0 or block_idx >= len(excluded):
            _update_job(
                session, job_uuid,
                status="failed",
                error=f"Block index {block_idx} out of range (0..{len(excluded)-1})",
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "reason": "block_out_of_range"}

        try:
            pdf_bytes = download_pdf(book.pdf_url)
            index = SchemaIndex.from_schema(schema_dict)

            _update_job(session, job_uuid, progress=40, message="OCR pass")

            if is_inline:
                # Rebuild existing_keys EXCLUDING this inline block (we're about
                # to replace its rows, so they shouldn't dedupe against themselves).
                all_rows = session.execute(
                    select(Question.page_start, Question.raw_text, Question.excluded_block_index)
                    .where(Question.bank_id == bank.id)
                ).all()
                existing_keys = {
                    (r[0], (r[1] or "")[:120])
                    for r in all_rows
                    if r[2] != block_idx
                }
                block_stats = _extract_one_inline(
                    session=session,
                    bank=bank,
                    book=book,
                    pdf_bytes=pdf_bytes,
                    inline_idx=block_idx - INLINE_BLOCK_BASE,
                    target=inline_targets[block_idx - INLINE_BLOCK_BASE],
                    existing_keys=existing_keys,
                    job_uuid=job_uuid,
                    progress=50,
                )
            else:
                block_stats = _extract_one_block(
                    session=session,
                    bank=bank,
                    book=book,
                    pdf_bytes=pdf_bytes,
                    index=index,
                    block_idx=block_idx,
                    ex=excluded[block_idx],
                    job_uuid=job_uuid,
                    progress=50,
                )

            bank.extraction_stats = _upsert_block_stats(
                bank.extraction_stats, block_stats
            )
            # If it was failed and this block recovered, keep bank "ready" once any block succeeded
            if bank.status != "ready":
                bank.status = "ready"
                bank.last_error = None
            session.commit()

            _update_job(
                session, job_uuid,
                status="succeeded",
                progress=100,
                message=(
                    f"{block_stats.get('extracted', 0)}/"
                    f"{block_stats.get('identified', 0)} extracted"
                ),
                finished_at=datetime.utcnow(),
            )
            return {"ok": True, "block_stats": block_stats}

        except Exception as e:
            logger.exception("re_extract_block_task failed")
            session.rollback()
            _update_job(
                session, job_uuid,
                status="failed",
                error=str(e)[:2000],
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "error": str(e)}


def _re_extract_block(bank_id: str, block_idx: int, job_id: str) -> dict:
    return re_extract_block_task(None, bank_id, block_idx, job_id)  # type: ignore[arg-type]


register_task("re_extract_block", _re_extract_block)


# ---------------------------------------------------------------------------
# Task: extract_questions_regen
#   Regenerate (or partially regenerate) questions for a bank into a separate
#   regen scope. Originals (regen_id IS NULL) are NEVER touched. Custom
#   instructions, when present, are injected as a HIGHEST PRIORITY OVERRIDE in
#   every per-block prompt.
# ---------------------------------------------------------------------------
def _filter_schema_to_section_refs(
    schema_dict: dict, section_refs: list[str] | None
) -> dict:
    """Return a shallow-copy of ``schema_dict`` with ``excluded_sections`` and
    ``sections`` pruned so only entries whose section_ref (id) is in
    ``section_refs`` survive. ``None``/empty means: no filter (whole bank).
    """
    if not section_refs:
        return schema_dict
    keep = {str(r) for r in section_refs}

    def keep_section(s: Any) -> bool:
        return isinstance(s, dict) and str(s.get("id") or "") in keep

    def prune_tree(arr: Any) -> list[dict]:
        out: list[dict] = []
        for s in arr or []:
            if not isinstance(s, dict):
                continue
            children = prune_tree(s.get("subsections") or [])
            if keep_section(s) or children:
                copy = dict(s)
                copy["subsections"] = children
                out.append(copy)
        return out

    excluded = list(schema_dict.get("excluded_sections") or [])
    excluded_filtered = [
        e
        for e in excluded
        if isinstance(e, dict)
        and (
            str(e.get("section_ref") or "") in keep
            or str(e.get("id") or "") in keep
        )
    ]
    out = dict(schema_dict)
    out["excluded_sections"] = excluded_filtered
    out["sections"] = prune_tree(schema_dict.get("sections") or [])
    return out


@celery_app.task(name="extract_questions_regen", bind=True)
def extract_questions_regen_task(self, regen_id: str, job_id: str) -> dict:
    from app.models.question_regeneration import QuestionRegeneration

    regen_uuid = UUID(regen_id)
    job_uuid = UUID(job_id)

    with SyncSession() as session:
        _update_job(
            session,
            job_uuid,
            status="running",
            started_at=datetime.utcnow(),
            message="Loading regen + bank + book",
            progress=2,
        )

        regen = session.get(QuestionRegeneration, regen_uuid)
        if regen is None:
            _update_job(
                session, job_uuid,
                status="failed", error="Regen row missing",
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "reason": "regen_missing"}

        bank = session.get(QuestionBank, regen.bank_id)
        book = session.get(Book, regen.book_id)
        if bank is None or book is None or not book.pdf_url or not book.schema:
            _update_job(
                session, job_uuid,
                status="failed",
                error="Bank/book/PDF/schema missing for regen",
                finished_at=datetime.utcnow(),
            )
            regen.status = "failed"
            regen.last_error = "missing prerequisite"
            session.commit()
            return {"ok": False, "reason": "prereq_missing"}

        regen.status = "extracting"
        regen.last_error = None
        session.commit()

        try:
            pdf_bytes = download_pdf(book.pdf_url)
            section_refs = (
                list(regen.section_refs or [])
                if regen.scope == "sections"
                else None
            )
            schema_dict = _filter_schema_to_section_refs(book.schema or {}, section_refs)
            index = SchemaIndex.from_schema(book.schema or {})
            excluded = list(schema_dict.get("excluded_sections") or [])
            inline_targets = _collect_inline_targets(schema_dict)
            covered_refs = {
                t["section_ref"] for t in inline_targets if t.get("section_ref")
            }
            sweep_targets = _collect_sweep_targets(schema_dict, covered_refs)
            total = len(excluded) + len(inline_targets) + len(sweep_targets)

            custom_instructions = (regen.custom_instructions or "").strip() or None

            if total == 0:
                regen.extraction_stats = {
                    "total_identified": 0,
                    "total_extracted": 0,
                    "missed": 0,
                    "blocks": [],
                }
                regen.status = "ready"
                regen.finished_at = datetime.utcnow()
                session.commit()
                _update_job(
                    session, job_uuid,
                    status="succeeded",
                    progress=100,
                    message="No extractable sections in regen scope",
                    finished_at=datetime.utcnow(),
                )
                return {"ok": True, "regen_id": str(regen.id), "total_questions": 0}

            block_rows: list[dict[str, Any]] = []
            step = 0
            bank_id_local = bank.id
            book_id_local = book.id
            regen_id_local = regen.id

            def _run_block(block_idx, ex, progress_now):
                with SyncSession() as own:
                    own_bank = own.get(QuestionBank, bank_id_local)
                    own_book = own.get(Book, book_id_local)
                    if own_bank is None or own_book is None:
                        raise RuntimeError("bank/book vanished mid-regen")
                    stats = _extract_one_block(
                        session=own, bank=own_bank, book=own_book,
                        pdf_bytes=pdf_bytes, index=index,
                        block_idx=block_idx, ex=ex,
                        job_uuid=job_uuid, progress=progress_now,
                        regen_id=regen_id_local,
                        custom_instructions=custom_instructions,
                    )
                    stats["source"] = "excluded"
                    return stats

            def _run_inline(inline_idx, target, source_label, shared_keys, progress_now):
                with SyncSession() as own:
                    own_bank = own.get(QuestionBank, bank_id_local)
                    own_book = own.get(Book, book_id_local)
                    if own_bank is None or own_book is None:
                        raise RuntimeError("bank/book vanished mid-regen")
                    stats = _extract_one_inline(
                        session=own, bank=own_bank, book=own_book,
                        pdf_bytes=pdf_bytes,
                        inline_idx=inline_idx, target=target,
                        existing_keys=shared_keys,
                        job_uuid=job_uuid, progress=progress_now,
                        regen_id=regen_id_local,
                        custom_instructions=custom_instructions,
                    )
                    stats["source"] = source_label
                    return stats

            # Pass 1
            valid_excluded = [
                (i, ex) for i, ex in enumerate(excluded) if isinstance(ex, dict)
            ]
            base_p1 = 10 + int(85 * step / max(total, 1))
            _update_job(
                session, job_uuid,
                message=f"Regen Pass 1 — {len(valid_excluded)} blocks (×{BLOCK_CONCURRENCY})",
                progress=base_p1,
            )
            with ThreadPoolExecutor(max_workers=BLOCK_CONCURRENCY) as pool:
                futures = {
                    pool.submit(_run_block, idx, ex, base_p1): (idx, ex)
                    for idx, ex in valid_excluded
                }
                for fut in as_completed(futures):
                    try:
                        stats = fut.result()
                    except Exception:
                        logger.exception("regen Pass 1 block failed")
                        continue
                    block_rows.append(stats)
                    regen.extraction_stats = _compute_bank_totals(block_rows)
                    session.commit()
                    step += 1
                    _update_job(
                        session, job_uuid,
                        message=f"✓ {stats.get('title','?')[:40]} — {stats['extracted']}/{stats['identified']} ({step}/{total})",
                        progress=10 + int(85 * step / max(total, 1)),
                    )

            # Pass 2
            existing_keys = _existing_question_keys(session, bank.id, regen_id=regen.id)
            base_p2 = 10 + int(85 * step / max(total, 1))
            if inline_targets:
                _update_job(
                    session, job_uuid,
                    message=f"Regen Pass 2 — {len(inline_targets)} inline (×{BLOCK_CONCURRENCY})",
                    progress=base_p2,
                )
                with ThreadPoolExecutor(max_workers=BLOCK_CONCURRENCY) as pool:
                    futures = {
                        pool.submit(_run_inline, idx, t, "inline", existing_keys, base_p2): (idx, t)
                        for idx, t in enumerate(inline_targets)
                    }
                    for fut in as_completed(futures):
                        try:
                            stats = fut.result()
                        except Exception:
                            logger.exception("regen Pass 2 inline failed")
                            continue
                        block_rows.append(stats)
                        regen.extraction_stats = _compute_bank_totals(block_rows)
                        session.commit()
                        step += 1
                        _update_job(
                            session, job_uuid,
                            message=f"✓ §{stats.get('section_ref') or '?'} — {stats['extracted']} new ({step}/{total})",
                            progress=10 + int(85 * step / max(total, 1)),
                        )

            # Pass 3
            existing_keys = _existing_question_keys(session, bank.id, regen_id=regen.id)
            base_p3 = 10 + int(85 * step / max(total, 1))
            if sweep_targets:
                _update_job(
                    session, job_uuid,
                    message=f"Regen Pass 3 — {len(sweep_targets)} leaves (×{BLOCK_CONCURRENCY})",
                    progress=base_p3,
                )
                INLINE_BASE = 10000
                SWEEP_BASE = 20000
                with ThreadPoolExecutor(max_workers=BLOCK_CONCURRENCY) as pool:
                    futures = {}
                    for sweep_idx, target in enumerate(sweep_targets):
                        idx_for_sweep = SWEEP_BASE - INLINE_BASE + sweep_idx
                        f = pool.submit(_run_inline, idx_for_sweep, target, "sweep", existing_keys, base_p3)
                        futures[f] = (sweep_idx, target)
                    for fut in as_completed(futures):
                        try:
                            stats = fut.result()
                        except Exception:
                            logger.exception("regen Pass 3 sweep failed")
                            continue
                        block_rows.append(stats)
                        regen.extraction_stats = _compute_bank_totals(block_rows)
                        session.commit()
                        step += 1
                        _update_job(
                            session, job_uuid,
                            message=f"✓ sweep §{stats.get('section_ref') or '?'} ({step}/{total})",
                            progress=10 + int(85 * step / max(total, 1)),
                        )

            regen.status = "ready"
            regen.finished_at = datetime.utcnow()
            session.commit()

            totals = regen.extraction_stats or {}
            partial_n = int(totals.get("partial_blocks", 0))
            failed_n = int(totals.get("failed_blocks", 0))
            extracted = totals.get("total_extracted", 0)
            identified = totals.get("total_identified", 0)
            base = f"Regen extracted {extracted} of {identified} across {total} blocks"
            if partial_n or failed_n:
                base += (
                    f" — {partial_n} partial, {failed_n} failed. "
                    "Re-run regen on affected blocks if needed."
                )
            _update_job(
                session, job_uuid,
                status="succeeded",
                progress=100,
                message=base,
                finished_at=datetime.utcnow(),
            )
            return {
                "ok": True,
                "regen_id": str(regen.id),
                "total_questions": totals.get("total_extracted", 0),
            }

        except Exception as e:
            logger.exception("extract_questions_regen_task failed")
            session.rollback()
            regen = session.get(QuestionRegeneration, regen_uuid)
            if regen is not None:
                regen.status = "failed"
                regen.last_error = str(e)[:2000]
                regen.finished_at = datetime.utcnow()
                session.commit()
            _update_job(
                session, job_uuid,
                status="failed",
                error=str(e)[:2000],
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "error": str(e)}


def _extract_questions_regen(regen_id: str, job_id: str) -> dict:
    return extract_questions_regen_task(None, regen_id, job_id)  # type: ignore[arg-type]


register_task("extract_questions_regen", _extract_questions_regen)
