"""Question extraction worker.

Task: ``extract_questions`` — walk the book's schema, for each non-excluded section
slice the PDF to that page range, ask Gemini to OCR-extract any questions
(exercises, problems, Q&A items) verbatim, and persist them as Question rows
in a QuestionBank.

Completely independent of theory extraction — no dependency on Section rows,
no modification of the existing flow.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from datetime import datetime
from uuid import UUID

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.storage import download_pdf
from app.models.book import Book
from app.models.job import Job
from app.models.question import Question
from app.models.question_bank import QuestionBank
from app.schemas.analyser import BookSchema
from app.services.chunk_builder import flatten_sections
from app.services.section_identity import build_section_uuid_map
from app.services.prompt_loader import load_raw
from app.utils.json_parse import parse_json
from app.workers.celery_app import celery_app
from app.workers.runner import register as register_task

logger = logging.getLogger(__name__)

_sync_engine = create_engine(settings.SYNC_DATABASE_URL, pool_pre_ping=True)
SyncSession = sessionmaker(bind=_sync_engine, class_=Session, autoflush=False)

GEMINI_MODEL = "gemini-2.5-flash"
MAX_ATTEMPTS = 2


def _update_job(session: Session, job_id: UUID, **fields) -> None:
    job = session.get(Job, job_id)
    if job is None:
        return
    for k, v in fields.items():
        setattr(job, k, v)
    from datetime import datetime, timezone
    job.last_heartbeat_at = datetime.now(timezone.utc)
    session.commit()


def _get_api_key() -> str:
    api_key = os.environ.get("GEMINI_API_KEY") or ""
    if not api_key:
        try:
            api_key = settings.GEMINI_API_KEY
        except Exception:
            pass
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set")
    return api_key


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
    """Upload PDF slice to Gemini, return raw JSON text."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=_get_api_key())
    tmp_path = None
    uploaded_file = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_slice)
            tmp_path = tmp.name

        with open(tmp_path, "rb") as f:
            uploaded_file = client.files.upload(
                file=f,
                config=types.UploadFileConfig(
                    mime_type="application/pdf",
                    display_name="section.pdf",
                ),
            )

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                types.Part.from_uri(
                    file_uri=uploaded_file.uri,
                    mime_type="application/pdf",
                ),
                system_prompt + "\n\n" + user_prompt,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0,
                max_output_tokens=32000,
            ),
        )
        return response.text or ""
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        if uploaded_file is not None:
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception:
                pass


def _build_user_prompt(section_id: str, title: str, page_start: int | None, page_end: int | None) -> str:
    return (
        f"Extract ALL questions printed on pages {page_start}-{page_end} (section \"{title}\", ID: {section_id}).\n\n"
        "Transcribe each question verbatim — every question number, sub-part, option, and printed answer.\n"
        "If these pages contain ONLY theory/explanation and no questions, return {\"questions\": []}.\n"
        "Return valid JSON matching the output format from the system prompt."
    )


async def _extract_questions_for_section(
    pdf_bytes: bytes,
    section_id: str,
    title: str,
    page_start: int | None,
    page_end: int | None,
) -> tuple[list[dict], int, list[str]]:
    """Run Gemini with retries. Returns (questions, attempts, failures)."""
    system_prompt = load_raw("question_extractor")
    user_prompt = _build_user_prompt(section_id, title, page_start, page_end)
    pdf_slice = _slice_pdf(pdf_bytes, page_start, page_end)

    failures: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = await asyncio.to_thread(
                _call_gemini_sync, pdf_slice, system_prompt, user_prompt
            )
            data = parse_json(raw)
            questions = list(data.get("questions") or [])
            return questions, attempt, failures
        except Exception as e:
            msg = f"attempt {attempt}: {e}"
            logger.warning("Question extraction error (%s) %s", section_id, msg)
            failures.append(msg[:300])
    return [], MAX_ATTEMPTS, failures


@celery_app.task(name="extract_questions", bind=True)
def extract_questions_task(self, book_id: str, job_id: str) -> dict:
    """Extract questions from a book into its pending QuestionBank.

    Looks up the most recent QuestionBank for this book (status="pending" or
    "extracting"), iterates every non-excluded schema section, asks Gemini to
    OCR questions, and persists them.
    """
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
                session,
                job_uuid,
                status="failed",
                error="Book or PDF missing",
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "reason": "book_missing"}
        if not book.schema:
            _update_job(
                session,
                job_uuid,
                status="failed",
                error="Book schema missing — run /analyse first",
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "reason": "schema_missing"}

        # Find the most recent bank awaiting extraction for this book
        bank = session.execute(
            select(QuestionBank)
            .where(QuestionBank.book_id == book_uuid)
            .where(QuestionBank.status.in_(["pending", "extracting"]))
            .order_by(QuestionBank.created_at.desc())
        ).scalars().first()
        if bank is None:
            _update_job(
                session,
                job_uuid,
                status="failed",
                error="No pending QuestionBank for this book",
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "reason": "no_bank"}

        bank.status = "extracting"
        session.commit()

        try:
            pdf_bytes = download_pdf(book.pdf_url)
            schema = BookSchema(**book.schema)
            sections = flatten_sections(schema)
            if not sections:
                raise RuntimeError("No sections in schema")

            total = len(sections)
            total_questions = 0

            # Map slug -> Section UUID once for this book (CONTRACT.md §1)
            section_uuid_map = build_section_uuid_map(session, book_uuid)

            for i, sec_schema in enumerate(sections, start=1):
                progress = 10 + int(85 * (i - 1) / max(total, 1))
                _update_job(
                    session,
                    job_uuid,
                    message=f"Scanning {sec_schema.title} ({i}/{total})",
                    progress=progress,
                )

                # Trim container sections so we don't double-scan children pages
                next_sec = sections[i] if i < total else None
                is_container = len(sec_schema.subsections) > 0
                if (
                    is_container
                    and next_sec
                    and next_sec.page_start is not None
                    and sec_schema.page_end is not None
                ):
                    effective_page_end = min(sec_schema.page_end, next_sec.page_start)
                else:
                    effective_page_end = sec_schema.page_end

                questions, attempts, failures = asyncio.run(
                    _extract_questions_for_section(
                        pdf_bytes=pdf_bytes,
                        section_id=sec_schema.id,
                        title=sec_schema.title,
                        page_start=sec_schema.page_start,
                        page_end=effective_page_end,
                    )
                )

                # Wipe old rows for this bank + section (re-runs are idempotent)
                session.execute(
                    delete(Question).where(
                        Question.bank_id == bank.id,
                        Question.section_ref == sec_schema.id,
                    )
                )

                # Local QC: at least one question with >=5 words
                valid = [
                    q for q in questions
                    if isinstance(q, dict)
                    and isinstance(q.get("raw_text"), str)
                    and len((q["raw_text"] or "").split()) >= 5
                ]
                qc_pass = bool(valid)
                qc_local = {
                    "pass": qc_pass,
                    "score": 1.0 if qc_pass else 0.0,
                    "failures": failures if failures else ([] if valid else ["No questions found"]),
                }

                if not valid:
                    # Skipped — sections without questions are normal
                    session.commit()
                    continue

                for q in valid:
                    raw_text = q["raw_text"].strip()
                    page = q.get("page")
                    try:
                        page_num = int(page) if page is not None else None
                    except (TypeError, ValueError):
                        page_num = None
                    row = Question(
                        bank_id=bank.id,
                        book_id=book_uuid,
                        section_ref=sec_schema.id,
                        section_uuid=section_uuid_map.get(sec_schema.id),
                        section_title=sec_schema.title,
                        page_start=page_num if page_num is not None else sec_schema.page_start,
                        page_end=page_num if page_num is not None else effective_page_end,
                        raw_text=raw_text,
                        qc_local=qc_local,
                        attempts=attempts,
                        status="passed",
                    )
                    session.add(row)
                    total_questions += 1

                session.commit()

            bank.status = "ready"
            session.commit()

            _update_job(
                session,
                job_uuid,
                status="succeeded",
                progress=100,
                message=f"Extracted {total_questions} questions from {total} sections",
                finished_at=datetime.utcnow(),
            )
            return {"ok": True, "bank_id": str(bank.id), "total_questions": total_questions}

        except Exception as e:
            logger.exception("extract_questions_task failed")
            session.rollback()
            bank = session.get(QuestionBank, bank.id)
            if bank is not None:
                bank.status = "failed"
                session.commit()
            _update_job(
                session,
                job_uuid,
                status="failed",
                error=str(e)[:2000],
                finished_at=datetime.utcnow(),
            )
            return {"ok": False, "error": str(e)}


def _extract_questions(book_id: str, job_id: str) -> dict:
    return extract_questions_task(None, book_id, job_id)  # type: ignore[arg-type]


# ─── DECOMMISSIONED ──────────────────────────────────────────────────────
# The v1 question worker is no longer dispatched anywhere. All callers
# (orchestrator's _dispatch_questions, /api/question_banks dispatch, and
# main.py orphan recovery) route to extract_questions_v3.
#
# The v3 worker (app.workers.questions_v3) carries all today's question
# improvements: Cat B section skip (the architectural guarantee that
# theory sections never get scanned for questions), Tier A completeness
# threshold (1.0), verification loop after Pass-3, wrapper rule for
# Cat A parents, page-by-page Pass-3 fallback, latex_normalize logging.
# Keeping v1 registered would silently bypass all of that if any caller
# accidentally dispatched it.
#
# The v1 code is kept in this file for archaeology only — function bodies
# remain importable so historical tests can still construct rows, but
# `register_task` is deliberately commented out so the dispatcher cannot
# reach v1 via the runner. If you ever need to truly resurrect v1, you'd
# have to consciously re-enable the registration AND update orphan
# recovery routing — both safety nets agree on "v3 only".
# register_task("extract_questions", _extract_questions)   # DEAD CODE — do not re-enable

