"""Books router — upload, list, get, delete, analyse, schema patch, approve, export."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.rate_limit import extraction_limit
from app.core.storage import upload_pdf
from app.models.book import Book
from app.models.job import Job
from app.models.regeneration import Regeneration
from app.models.section import Section
from app.schemas.analyser import BookSchema
from app.schemas.book import BookOut, BookUploadResponse

router = APIRouter(prefix="/api/books", tags=["books"])


# ─────────────────────────────────────────────────────────────────────
# Shared markdown builder (used by /export/markdown and /export/docx)
# ─────────────────────────────────────────────────────────────────────

_LEADING_NUM_RE = re.compile(r"^\s*(?:\(\s*\d+\s*\)|\d+[.)])\s+")
_HEADING_NORM_RE = re.compile(r"\s+")


def _strip_leading_number(s: str) -> str:
    """Strip baked-in ``1. ``/``2) ``/``(3) `` prefixes from list items."""
    return _LEADING_NUM_RE.sub("", s).strip()


def _norm_heading(s: str) -> str:
    """Lowercase + collapse whitespace + strip punctuation for heading compare."""
    s = _HEADING_NORM_RE.sub(" ", (s or "").strip().lower())
    return s.strip(" .:;—-")


def _build_markdown(
    book: Book,
    sections: list[Section],
    regen_blocks: dict[str, list],
    *,
    numbered_lists: bool = False,
) -> str:
    """Render extraction/regen blocks as Markdown.

    numbered_lists=True emits ``1. ``/``2. `` items so pandoc → docx produces
    a native numbered list. numbered_lists=False keeps the plain ``-`` bullets
    used by the existing .md download.
    """
    lines: list[str] = [f"# {book.title}", ""]
    for sec in sections:
        level = sec.level or 2
        hashes = "#" * min(level + 1, 6)
        lines.append(f"{hashes} {sec.section_id} {sec.title}")
        lines.append("")
        section_title_norm = _norm_heading(sec.title or "")
        blocks_to_render = regen_blocks.get(sec.section_id) if regen_blocks else None
        for block in (blocks_to_render if blocks_to_render is not None else sec.blocks or []):
            t = block.get("t")
            if t == "p":
                lines.append(block.get("c", ""))
                lines.append("")
            elif t == "h3":
                h_text = block.get("c", "")
                # Skip h3 blocks that simply repeat the section title — the
                # parent section heading already renders that text.
                if section_title_norm and _norm_heading(h_text) == section_title_norm:
                    continue
                lines.append(f"### {h_text}")
                lines.append("")
            elif t == "eq":
                lines.append("$$")
                lines.append(block.get("c", ""))
                lines.append("$$")
                lines.append("")
            elif t == "def":
                lines.append(f"**{block.get('term', 'Definition')}:** {block.get('c', '')}")
                lines.append("")
            elif t == "kp":
                lines.append(f"> **Key Point:** {block.get('c', '')}")
                lines.append("")
            elif t == "fig":
                lines.append(f"*[Figure: {block.get('c', '')}]*")
                lines.append("")
            elif t == "list":
                items = block.get("items", []) or []
                if numbered_lists:
                    for idx, item in enumerate(items, 1):
                        lines.append(f"{idx}. {_strip_leading_number(str(item))}")
                else:
                    for item in items:
                        lines.append(f"- {item}")
                lines.append("")
            elif t == "table":
                caption = block.get("caption", "")
                if caption:
                    lines.append(f"*{caption}*")
                headers = block.get("headers", [])
                rows = block.get("rows", [])
                if headers:
                    lines.append("| " + " | ".join(headers) + " |")
                    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
                for row in rows:
                    lines.append("| " + " | ".join(str(c) for c in row) + " |")
                lines.append("")
            elif t == "example":
                lines.append(f"**Example — {block.get('label', '')}:** {block.get('prob', '')}")
                for eq in block.get("eqs", []):
                    lines.append(f"$$\n{eq}\n$$")
                lines.append("")
    return "\n".join(lines)


def _ensure_pandoc_on_path() -> str:
    """Locate the pandoc binary for pypandoc. Returns the resolved path."""
    found = shutil.which("pandoc")
    if found:
        return found
    # Common install locations that may not be on the launchd PATH
    candidates = [
        Path.home() / ".local/bin/pandoc",
        Path("/opt/homebrew/bin/pandoc"),
        Path("/usr/local/bin/pandoc"),
    ]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            os.environ["PATH"] = f"{c.parent}:{os.environ.get('PATH', '')}"
            os.environ.setdefault("PYPANDOC_PANDOC", str(c))
            return str(c)
    raise HTTPException(
        500,
        detail="Pandoc binary not found. Install pandoc or set PYPANDOC_PANDOC.",
    )


@router.post("", response_model=BookUploadResponse, status_code=status.HTTP_201_CREATED)
async def create_book(
    file: UploadFile = File(...),
    title: str | None = Form(None),
    folder_id: UUID | None = Form(None),
    subject: str | None = Form(None),
    # User-set flag at upload time for multi-column PDFs (MHT-CET, JEE
    # prep, dense question banks). When True, the analyse worker routes
    # to the multi-column-aware schema prompt that enforces per-column
    # reading order + per-heading classification so dense MCQ pages
    # don't get mis-tagged as "all explanations" and silently dropped.
    # Stored in book.analyser JSON (no migration needed). Default False
    # → single-column behaviour unchanged.
    is_multi_column: bool = Form(False),
    session: AsyncSession = Depends(get_session),
) -> BookUploadResponse:
    # Accept by content-type OR by .pdf extension (browsers sometimes send
    # application/octet-stream when dragging from certain folders).
    filename = (file.filename or "").lower()
    is_pdf = (
        file.content_type in ("application/pdf", "application/x-pdf")
        or filename.endswith(".pdf")
    )
    if not is_pdf:
        raise HTTPException(
            400,
            detail=f"Expected a PDF file (got content-type {file.content_type!r}, filename {file.filename!r}).",
        )

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(400, detail="Empty file")

    pdf_key = upload_pdf(pdf_bytes, file.filename or "document.pdf")

    book = Book(
        title=title or (file.filename or "Untitled").rsplit(".", 1)[0],
        pdf_url=pdf_key,
        status="uploaded",
        folder_id=folder_id,
        subject=subject,
        # Stash the multi-column flag in analyser JSON so analyse_book_task
        # can read it before generating the schema. analyser is otherwise
        # populated by the worker with AnalyserResult fields; we pre-seed
        # this one field, and the worker preserves it on overwrite.
        analyser={"is_multi_column": True} if is_multi_column else None,
    )
    session.add(book)
    await session.flush()

    return BookUploadResponse(book_id=book.id, job_id=None, status="uploaded")


@router.get("", response_model=list[BookOut])
async def list_books(session: AsyncSession = Depends(get_session)) -> list[BookOut]:
    result = await session.execute(select(Book).order_by(Book.created_at.desc()))
    return [BookOut.from_orm_book(b) for b in result.scalars().all()]


@router.get("/{book_id}", response_model=BookOut)
async def get_book(book_id: UUID, session: AsyncSession = Depends(get_session)) -> BookOut:
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")
    # Self-heal stuck books — if the worker died mid-flight and left
    # book.status="schema_ready" but every theory-bearing section actually
    # finished (passed/failed), promote the book to "ready" so the UI
    # unblocks. Idempotent: only flips schema_ready → ready, never the
    # other direction.
    if book.status == "schema_ready":
        from sqlalchemy import func, select as _select
        from app.models import Section
        counts = (
            await session.execute(
                _select(Section.status, func.count(Section.id))
                .where(Section.book_id == book_id)
                .group_by(Section.status)
            )
        ).all()
        by_status = {s: int(n) for s, n in counts}
        total = sum(by_status.values())
        terminal = by_status.get("passed", 0) + by_status.get("failed", 0)
        # All sections in a terminal state and at least one passed → ready.
        if total > 0 and terminal == total and by_status.get("passed", 0) > 0:
            book.status = "ready"
            await session.commit()
            await session.refresh(book)
    return BookOut.from_orm_book(book)


@router.get("/{book_id}/quality")
async def get_book_quality(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Phase 5c (CONTRACT.md §3 — Verification Contract).

    Read-only quality report — what does the book actually contain
    vs what the schema promised? Use to detect "ready with empty
    content" lies, missing sections, unattached figures.

    Does NOT mutate. Safe to call repeatedly. Caches nothing.

    Response shape: see app/services/verify_book.py docstring.
    """
    from app.services.verify_book import verify_book
    return await verify_book(session, book_id)


@router.post("/{book_id}/cancel")
async def cancel_book(
    book_id: UUID, session: AsyncSession = Depends(get_session)
) -> dict:
    """Stop a book's extraction FOR REAL and keep it stopped.

    Kills the running Celery task(s) for this book and moves the book to a
    terminal ``cancelled`` status so the state-driven driver won't restart it.
    After this the book is immediately deletable (no in-flight jobs remain).
    No backend restart required — this is the production cancel path.
    """
    from app.services.cancellation import cancel_books

    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")
    result = await cancel_books(session, book_ids=[book_id], reason="Cancelled by user")
    return {"book_id": str(book_id), **result}


@router.delete("/{book_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_book(book_id: UUID, session: AsyncSession = Depends(get_session)) -> None:
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")
    # Phase 7 (CONTRACT.md §5 — Atomicity & Concurrency): refuse to delete
    # a book that has an in-flight Celery task. The zombie-task bug we hit
    # today (15-min ObjectDeletedError grind) was caused by deleting a
    # book while extract_book was running — the task kept hitting deleted
    # row commits.
    #
    # 409 Conflict tells the user explicitly: "this book has running work
    # — wait for it to finish or cancel the job first". This is preferable
    # to silently letting the worker spin uselessly until it errors out.
    in_flight = (await session.execute(
        select(Job).where(
            Job.book_id == book_id,
            Job.status.in_(["queued", "running"]),
        ).limit(1)
    )).scalars().first()
    if in_flight is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"Book has an in-flight task (job_id={in_flight.id}, "
                f"status={in_flight.status!r}). Wait for it to finish or "
                "cancel it before deleting."
            ),
        )
    await session.delete(book)


@router.post(
    "/{book_id}/analyse",
    response_model=BookUploadResponse,
    dependencies=[Depends(extraction_limit)],
)
async def analyse_book(
    book_id: UUID,
    force: bool = False,
    session: AsyncSession = Depends(get_session),
) -> BookUploadResponse:
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")
    if not book.pdf_url:
        raise HTTPException(400, detail="Book has no associated PDF")

    # ─── Pipeline state invariant guard ──────────────────────────────
    # The previous unconditional reset of ALL stages to "pending" caused
    # the analyse #3 bug: when this endpoint fired AFTER downstream stages
    # had completed (e.g. orchestrator retry, double-click, reconciliation
    # poke), it wiped theory_status / questions_status / figures_status =
    # "done" → orchestrator saw schema=pending → dispatched ANOTHER
    # analyse → duplicate Gemini call + corrupted UI showing schema 30%
    # while theory/questions already complete.
    #
    # Architectural fix: enforce monotonic-forward state. Refuse the
    # reset when downstream is in flight or has data. Re-extraction is
    # an EXPLICIT user action — exposed via /re-extract endpoint (full
    # cascade) or this endpoint with ?force=true (acknowledged intent).
    #
    # Three cases:
    #   1. Any stage `running` → 409. Pipeline in motion; restart would
    #      orphan in-flight workers.
    #   2. Downstream stage `done` and not `force=true` → 409. Caller
    #      must opt in to destroying completed work.
    #   3. All stages pending OR schema-only states → safe to dispatch,
    #      no reset needed.
    in_flight = (
        book.schema_status == "running"
        or book.theory_status == "running"
        or book.questions_status == "running"
        or book.figures_status == "running"
    )
    if in_flight:
        raise HTTPException(
            status_code=409,
            detail=(
                "Pipeline already in motion (a stage is running). Wait for "
                "completion before re-analysing, or call /re-extract for a "
                "full cascading reset."
            ),
        )

    downstream_done = (
        book.theory_status in ("done", "partial", "needs_review")
        or book.questions_status in ("done", "partial")
        or book.figures_status in ("done", "partial")
    )
    if downstream_done and not force:
        raise HTTPException(
            status_code=409,
            detail=(
                "Downstream stages already extracted. Re-analysing would "
                "destroy completed work. Call /re-extract for a full cascading "
                "reset, or POST /analyse?force=true to confirm this intent."
            ),
        )

    # Only reset when safe — all pending, OR force=true was explicitly
    # passed (caller acknowledged destroying downstream work).
    book.schema_status = "pending"
    book.theory_status = "pending"
    book.questions_status = "pending"
    book.figures_status = "pending"
    book.theory_finalized_at = None
    await session.commit()

    # Route through the orchestrator's atomic dispatcher. The CAS inside
    # guarantees only one analyse job can be created per book at a time;
    # a duplicate POST returns 409 instead of spawning a second worker.
    # This replaced the older TOCTOU "SELECT then INSERT" Job-table
    # check, which was racy under concurrent requests.
    import app.workers.extract  # noqa: F401 — registrations
    import app.workers.orchestrator  # noqa: F401 — registrations
    from app.workers.orchestrator import _dispatch_analyse, SyncSession
    from app.models.book import Book as BookModel

    # The orchestrator helper is sync (Celery world). Bridge by opening
    # a sync session, loading the book, and running the dispatch there.
    def _do_dispatch() -> UUID | None:
        with SyncSession() as s:
            b = s.get(BookModel, book_id)
            if b is None:
                return None
            return _dispatch_analyse(s, b)

    import asyncio
    job_id = await asyncio.to_thread(_do_dispatch)
    if job_id is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                "Already analysing (a sibling request won the race). "
                "Wait for it to finish or use /re-extract for a hard reset."
            ),
        )

    return BookUploadResponse(book_id=book.id, job_id=job_id, status="analysing")


@router.patch("/{book_id}/schema", response_model=BookOut)
async def patch_schema(
    book_id: UUID,
    schema: dict[str, Any] = Body(...),
    session: AsyncSession = Depends(get_session),
) -> BookOut:
    """User-edited schema — validates against BookSchema then persists."""
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")
    try:
        validated = BookSchema(**schema)
    except Exception as e:
        raise HTTPException(400, detail=f"Invalid schema: {e}") from e

    # Lock previously-extracted section_ids — schema edits must NEVER
    # mint a new ID for a section that already has DB content. The
    # alignment helper matches each node by (title + page range) against
    # existing DB Section rows and rewrites the node.id back to the
    # extraction-time id. Drift is impossible by construction after
    # this step; figure_references + questions joins stay valid.
    from app.services.schema_alignment import (
        align_schema_ids_to_existing_sections,
    )
    existing = (
        await session.execute(
            select(Section).where(Section.book_id == book.id)
        )
    ).scalars().all()
    validated, _remap = align_schema_ids_to_existing_sections(
        validated, existing
    )

    book.schema = validated.model_dump()
    book.title = validated.document_title or book.title
    book.subject = validated.subject or book.subject
    await session.flush()
    # Auto-relink theory chips so that manual schema edits (drag-drop in
    # the editor that moves an Example/Exercise to a different parent)
    # take effect on the theory page WITHOUT requiring a re-extract.
    # Non-fatal: if linker fails, the schema PATCH still succeeds.
    try:
        from app.services.example_linker import link_examples_to_theory
        await link_examples_to_theory(session, book.id)
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "post-patch chip relink failed (book=%s): %s", book.id, e,
        )
    # Re-run figure embedder too — schema edits can move a figure's
    # parent section, so placement metadata needs to be recomputed.
    try:
        from app.services.figure_embedder import embed_figures_for_book
        await embed_figures_for_book(session, book.id)
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "post-patch figure embedder failed (book=%s): %s", book.id, e,
        )
    # Ensure all attributes are loaded inside the async context — otherwise
    # Pydantic's from_attributes=True serialization in `from_orm_book` /
    # response_model can trigger lazy IO during the response phase →
    # sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called.
    await session.refresh(book)
    return BookOut.from_orm_book(book)


@router.post(
    "/{book_id}/approve",
    response_model=BookUploadResponse,
    dependencies=[Depends(extraction_limit)],
)
async def approve_schema(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> BookUploadResponse:
    """Approve the schema and kick off extraction via the orchestrator.

    ORCH Day 8 — was: directly dispatched extract_book and created a
    Job for it; now: dispatches the post-schema coordinator, which
    decides what to run next based on current book state. The
    coordinator is idempotent — if analyse_book already fired it
    (Day 3), this second call either no-ops (theory already running)
    or advances the state machine. Race-free either way.

    Backward-compat: frontend still receives {book_id, job_id, status}
    so polling continues to work. The Job row is logged as
    "succeeded" because the approval action itself is just a routing
    decision — the actual extraction Jobs are created by the
    coordinator per worker it dispatches.
    """
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")
    if not book.schema:
        raise HTTPException(400, detail="Book has no schema — run /analyse first")

    # Approval marker Job — completes immediately. The actual extraction
    # Jobs are created by the orchestrator's dispatcher functions.
    job = Job(
        book_id=book.id,
        type="extract",
        status="succeeded",
        progress=100,
        message="Approval routed to orchestrator",
    )
    session.add(job)
    await session.flush()

    book.status = "extracting"
    await session.commit()

    import app.workers.orchestrator  # noqa: F401 — ensure inline registration
    from app.workers.runner import dispatch

    # Idempotent — coordinator's lock + state machine handle the case
    # where analyse_book_task already dispatched the coordinator on
    # schema completion.
    dispatch("coordinate_extraction", str(book.id))
    return BookUploadResponse(book_id=book.id, job_id=job.id, status="extracting")


@router.post(
    "/{book_id}/re-extract",
    response_model=BookUploadResponse,
    dependencies=[Depends(extraction_limit)],
)
async def re_extract_book(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> BookUploadResponse:
    """Re-run full extraction on a book regardless of current status.

    Resets all section statuses to pending so the extraction loop
    processes every section fresh with the current extraction logic.
    """
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")
    if not book.schema:
        raise HTTPException(400, detail="Book has no schema — run /analyse first")

    # Atomic CAS — only proceed if NO stage is currently running AND no
    # fresh orchestrator lock is held. This single UPDATE…WHERE replaces
    # the previous "SELECT then UPDATE" pattern which had a race window:
    # two near-simultaneous re-extract clicks both saw "no in-flight",
    # both cleared `extraction_lock_at=None`, both spawned theory workers.
    # Now: rapid double-clicks → only ONE wins. The losers get a 409.
    from sqlalchemy import update
    from datetime import datetime, timedelta
    from app.models.section import Section

    lock_cutoff = datetime.utcnow() - timedelta(minutes=10)
    cas = (
        update(Book)
        .where(Book.id == book_id)
        # Refuse if any per-stage worker is mid-run
        .where(Book.theory_status != "running")
        .where(Book.questions_status != "running")
        .where(Book.figures_status != "running")
        # Refuse if a fresh orchestrator lock is held
        .where(sa.or_(
            Book.extraction_lock_at.is_(None),
            Book.extraction_lock_at < lock_cutoff,
        ))
        # ORCH Day 9 — full hard reset. Per-stage statuses → pending.
        # Orchestrator state cleared so the coordinator treats this as
        # a brand-new run with a fresh retry budget.
        .values(
            status="extracting",
            theory_status="pending",
            questions_status="pending",
            figures_status="pending",
            theory_finalized_at=None,
            extraction_lock_at=None,
            theory_retries=0,
            questions_retries=0,
            figures_retries=0,
        )
    )
    result = await session.execute(cas)
    if result.rowcount == 0:
        # CAS lost — another re-extract is already in flight, OR a worker
        # is currently running on this book, OR a fresh orchestrator lock
        # is held. Either way, refuse without touching state.
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                "Book has an in-flight extraction (worker running or "
                "another re-extract just dispatched). Wait for it to "
                "finish before re-extracting."
            ),
        )

    # Belt-and-suspenders — also catch in-flight Jobs that aren't reflected
    # in stage_status yet (very narrow race between Job INSERT and stage CAS).
    in_flight = (await session.execute(
        select(Job).where(
            Job.book_id == book_id,
            Job.status.in_(["queued", "running"]),
            Job.type.in_(["extract", "extract_questions", "extract_figures"]),
        ).limit(1)
    )).scalars().first()
    if in_flight is not None:
        # Roll back the CAS we just won — another path is already running.
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"Book has an in-flight extraction job (job_id={in_flight.id}, "
                f"type={in_flight.type!r}, status={in_flight.status!r}). "
                f"Wait for it to finish before re-extracting."
            ),
        )

    # CAS won AND no live Job — safe to wipe sections + commit + dispatch.
    await session.execute(
        update(Section)
        .where(Section.book_id == book_id)
        .values(status="pending", blocks=[], attempts=0, qc_local=None)
    )

    # ORCH Day 9 — approval marker Job. Actual extraction Jobs are created
    # by the coordinator's per-worker dispatchers. Matches the /approve pattern.
    job = Job(
        book_id=book.id, type="extract",
        status="succeeded", progress=100,
        message="Re-extract routed to orchestrator",
    )
    session.add(job)
    await session.flush()

    # Commit before dispatch so the coordinator sees the cleared state.
    await session.commit()

    import app.workers.orchestrator  # noqa: F401 — ensure registration
    from app.workers.runner import dispatch

    # Coordinator sees schema=done, theory=pending → fires extract_book.
    # Idempotent + lock-protected — safe even if another coordinator
    # dispatch was in flight from a different code path.
    dispatch("coordinate_extraction", str(book.id))
    return BookUploadResponse(book_id=book.id, job_id=job.id, status="extracting")


# ─── Day 10 — Per-stage retry endpoints ─────────────────────────────
#
# When ONE extraction stage fails (e.g. figures only), these endpoints
# retry just that stage without wiping the others. Section data from
# successful stages is preserved. The coordinator's lock + state
# machine deduplicate against any concurrent dispatch.


async def _dispatch_stage_retry(
    book: Book,
    stage: str,
    session: AsyncSession,
) -> BookUploadResponse:
    """Reset a single stage to pending + clear orchestrator state for it,
    then route through the coordinator. Shared helper for the 3 stage
    retry endpoints below.

    `stage` is one of 'theory', 'questions', 'figures'.

    Caller has already validated the book exists, has a schema, and
    that the given stage is in 'failed' state.
    """
    # Reset just this stage
    if stage == "theory":
        book.theory_status = "pending"
        book.theory_retries = 0
        book.theory_finalized_at = None
    elif stage == "questions":
        book.questions_status = "pending"
        book.questions_retries = 0
    elif stage == "figures":
        book.figures_status = "pending"
        book.figures_retries = 0
    else:  # pragma: no cover — kept for completeness
        raise ValueError(f"unknown stage: {stage!r}")

    # Force-release any stale lock so the coordinator can step the
    # state machine. Watchdog should have done this already after
    # 10 min, but the user-initiated retry shouldn't have to wait.
    book.extraction_lock_at = None
    book.status = "extracting"

    # Approval-marker Job (same pattern as /approve and /re-extract).
    job = Job(
        book_id=book.id, type=f"retry_{stage}",
        status="succeeded", progress=100,
        message=f"Retry {stage} routed to orchestrator",
    )
    session.add(job)
    await session.flush()
    await session.commit()

    import app.workers.orchestrator  # noqa: F401 — ensure inline registration
    from app.workers.runner import dispatch

    dispatch("coordinate_extraction", str(book.id))
    return BookUploadResponse(book_id=book.id, job_id=job.id, status="extracting")


async def _retry_stage_endpoint(
    book_id: UUID,
    stage: str,
    session: AsyncSession,
) -> BookUploadResponse:
    """Shared validation + dispatch path for the 3 stage retry endpoints."""
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")
    if not book.schema:
        raise HTTPException(400, detail="Book has no schema — run /analyse first")

    current = getattr(book, f"{stage}_status", None)
    if current not in ("failed", "partial"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"{stage}_status is {current!r}, not 'failed'/'partial' — "
                f"nothing to retry. Use /re-extract for a full reset."
            ),
        )

    return await _dispatch_stage_retry(book, stage, session)


@router.post(
    "/{book_id}/retry-theory",
    response_model=BookUploadResponse,
    dependencies=[Depends(extraction_limit)],
)
async def retry_theory(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> BookUploadResponse:
    """Retry just theory extraction, preserving questions + figures data.

    409 if theory_status is not 'failed' or 'partial'. Resets theory_status,
    clears theory_finalized_at, zeros theory_retries (so auto-retry budget is
    fresh), then dispatches the coordinator. 'partial' is accepted so a book
    left incomplete by a soft section failure can be re-driven to 'done'.
    """
    return await _retry_stage_endpoint(book_id, "theory", session)


@router.post(
    "/{book_id}/retry-questions",
    response_model=BookUploadResponse,
    dependencies=[Depends(extraction_limit)],
)
async def retry_questions(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> BookUploadResponse:
    """Retry just questions extraction, preserving theory + figures data.

    409 if questions_status is not 'failed' or 'partial'. Resets
    questions_status, zeros questions_retries, dispatches coordinator. The
    coordinator's _dispatch_questions handler creates a fresh QuestionBank row
    + supersedes prior pending banks.
    """
    return await _retry_stage_endpoint(book_id, "questions", session)


@router.post(
    "/{book_id}/retry-figures",
    response_model=BookUploadResponse,
    dependencies=[Depends(extraction_limit)],
)
async def retry_figures(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> BookUploadResponse:
    """Retry just figure extraction, preserving theory + questions data.

    409 if figures_status is not 'failed' or 'partial'. Resets figures_status,
    zeros figures_retries, dispatches coordinator.
    """
    return await _retry_stage_endpoint(book_id, "figures", session)


async def _load_export_context(
    book_id: UUID,
    regen_id: UUID | None,
    session: AsyncSession,
) -> tuple[Book, list[Section], dict[str, list]]:
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")

    regen_blocks: dict[str, list] = {}
    if regen_id is not None:
        regen = await session.get(Regeneration, regen_id)
        if regen and regen.book_id == book_id:
            regen_blocks = dict(regen.blocks_by_section or {})

    result = await session.execute(
        select(Section).where(Section.book_id == book_id)
    )
    sections_by_id = {s.section_id: s for s in result.scalars().all()}

    # Order sections by the schema's hierarchical sequence (pre-order walk),
    # not lexicographic section_id — otherwise "8.10" would sort before "8.2".
    # Also: skip container sections that have non-excluded subsections — their
    # content is fully represented by the child sections, including them would
    # duplicate every paragraph in the export.
    ordered: list[Section] = []
    if book.schema:
        try:
            from app.services.chunk_builder import flatten_sections as _flatten
            schema_obj = BookSchema(**book.schema)
            seen: set[str] = set()
            skipped_containers: set[str] = set()
            for ss in _flatten(schema_obj):
                has_live_children = any(
                    c.type != "excluded" for c in (ss.subsections or [])
                )
                if has_live_children:
                    skipped_containers.add(ss.id)
                    continue  # children will carry this section's content
                sec = sections_by_id.get(ss.id)
                if sec is not None and ss.id not in seen:
                    ordered.append(sec)
                    seen.add(ss.id)
            # Append any DB-only sections (defensive) at the end — but NOT the
            # containers we deliberately skipped above.
            for sid, sec in sections_by_id.items():
                if sid not in seen and sid not in skipped_containers:
                    ordered.append(sec)
        except Exception:
            ordered = list(sections_by_id.values())
    else:
        ordered = list(sections_by_id.values())

    # When exporting regenerated: only include sections that have regen blocks
    if regen_blocks:
        ordered = [s for s in ordered if s.section_id in regen_blocks]
    return book, ordered, regen_blocks


@router.get("/{book_id}/export/markdown")
async def export_book_markdown(
    book_id: UUID,
    regen_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Export sections as Markdown. Pass regen_id to export regenerated content."""
    book, sections, regen_blocks = await _load_export_context(book_id, regen_id, session)
    content = _build_markdown(book, sections, regen_blocks, numbered_lists=False)
    safe_name = re.sub(r"[^\w-]+", "_", book.title).strip("_") or "extraction"
    return Response(
        content=content.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.md"'},
    )


@router.get("/{book_id}/export/docx")
async def export_book_docx(
    book_id: UUID,
    regen_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Export theory sections as a Word (.docx) document using the native
    python-docx builder (`app.services.docx_export`). Replaces the
    pandoc pipeline so we control fonts, spacing, no-duplicate-heading
    invariant, and inline math/figure rendering precisely."""
    from app.services.docx_export import build_theory_docx

    book, sections, regen_blocks = await _load_export_context(book_id, regen_id, session)

    # Shape adapter: ORM Section rows + regen overrides → builder shape.
    payload: list[dict] = []
    for sec in sections:
        blocks = regen_blocks.get(sec.section_id) if regen_blocks else None
        if blocks is None:
            blocks = sec.blocks or []
        payload.append({
            "section_id": sec.section_id,
            "title": sec.title,
            "blocks": blocks,
        })

    data = build_theory_docx(book.title or "Theory Export", payload)
    safe_name = re.sub(r"[^\w-]+", "_", book.title or "extraction").strip("_") or "extraction"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.docx"'},
    )


@router.get("/{book_id}/export/json")
async def export_book_json(
    book_id: UUID,
    regen_id: UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Export sections as JSON. Pass regen_id to export regenerated content."""
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")

    regen_blocks: dict[str, list] = {}
    if regen_id is not None:
        regen = await session.get(Regeneration, regen_id)
        if regen and regen.book_id == book_id:
            regen_blocks = dict(regen.blocks_by_section or {})

    result = await session.execute(
        select(Section)
        .where(Section.book_id == book_id)
        .order_by(Section.section_id)
    )
    sections = result.scalars().all()

    # When exporting regenerated: only include sections that have regen blocks
    if regen_blocks:
        sections = [s for s in sections if s.section_id in regen_blocks]

    payload = {
        "title": book.title,
        "subject": book.subject,
        "schema": book.schema,
        "content_type": "regenerated" if regen_blocks else "original",
        "sections": [
            {
                "section_id": sec.section_id,
                "title": sec.title,
                "level": sec.level,
                "status": sec.status,
                "blocks": regen_blocks.get(sec.section_id, sec.blocks or []),
            }
            for sec in sections
        ],
    }

    safe_name = re.sub(r"[^\w-]+", "_", book.title).strip("_") or "extraction"
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.json"'},
    )
