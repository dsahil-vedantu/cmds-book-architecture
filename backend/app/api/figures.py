"""Figures pipeline v2 — API endpoints.

Routes:
  POST   /api/books/{book_id}/extract-figures-v2
         → dispatch extraction job

  GET    /api/books/{book_id}/figures
         → list figures grouped by section
         → only sections with >= 1 figure (Q4 — UI filter)
         → each figure tagged with theory|question contexts (Q4)

  GET    /api/figures/{figure_id}
         → full metadata (no image bytes)

  GET    /api/figures/{figure_id}/image?variant=original|regenerated
         → stream PNG bytes
         → variant=auto returns regenerated if exists, else original
           (Q7 — auto-policy at export time)

  POST   /api/books/{book_id}/sections/{section_ref}/regenerate-figures
         → dispatch section-scoped regen job (Q5)
         → body: { style, custom_instructions, watermark_clean, overlay,
                   image_model, ocr_model }

  POST   /api/figures/{figure_id}/discard-regen
         → clear regen variant, revert to original

  GET    /api/books/{book_id}/figure-references?section_ref=...
         → link table read (used by future export integration)
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.models.book import Book
from app.models.figure import Figure
from app.models.figure_reference import FigureReference
from app.models.figure_regeneration import FigureRegeneration
from app.models.job import Job

logger = logging.getLogger(__name__)

books_router = APIRouter(prefix="/api/books", tags=["figures-v2"])
figures_router = APIRouter(prefix="/api/figures", tags=["figures-v2"])


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

def _figure_dict(f: Figure, *, refs: list[FigureReference] | None = None) -> dict:
    return {
        "id": str(f.id),
        "book_id": str(f.book_id),
        "section_id": f.section_id,
        "figure_id_text": f.figure_id_text,
        "figure_number": f.figure_number,
        "normalized_label": f.normalized_label,
        "caption": f.caption,
        "description": f.description,
        "page_number": f.page_number,
        "bounding_box": f.bounding_box,
        "semantic_type": f.semantic_type,
        "tags": f.tags or [],
        "status": f.status,
        "regen_status": f.regen_status,
        "regen_version": f.regen_version,
        "has_original": bool(f.image_bytes),
        "has_regen": bool(f.regen_image_bytes),
        "regen_meta": f.regen_meta,
        "context_hint": f.context_hint,
        # 0016 — Q5 approval workflow
        "approved_at": f.approved_at.isoformat() if f.approved_at else None,
        "is_approved": bool(f.approved_at),
        "references": (
            [_ref_dict(r) for r in (refs or [])] if refs is not None else None
        ),
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


def _ref_dict(r: FigureReference) -> dict:
    return {
        "id": str(r.id),
        "figure_id": str(r.figure_id),
        "section_ref": r.section_ref,
        "context": r.context,
        "question_id": str(r.question_id) if r.question_id else None,
        "placeholder_text": r.placeholder_text,
        "link_method": r.link_method,
    }


# ---------------------------------------------------------------------------
# POST /api/books/{book_id}/extract-figures-v2
# ---------------------------------------------------------------------------

@books_router.post(
    "/{book_id}/extract-figures-v2",
    status_code=status.HTTP_201_CREATED,
)
async def extract_figures_v2(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Trigger whole-book figure extraction job.

    Idempotent: re-running wipes prior figures for this book in the worker
    (since we re-OCR with potentially-newer prompt versions).
    """
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")
    if not book.pdf_url:
        raise HTTPException(400, detail="Book has no uploaded PDF")

    # Race-free dispatch — route through the orchestrator's atomic
    # _dispatch_figures helper. The CAS inside ensures only one figures
    # job can be created at a time per book: a concurrent request (or a
    # race with the coordinator's auto-dispatch) gets 409 instead of
    # spawning a duplicate worker. Replaced the previous TOCTOU "SELECT
    # Job WHERE running" check, which was racy under concurrent POSTs.
    book.figures_status = "pending"  # reset so CAS sees pending → running
    await session.commit()

    import app.workers.figures_tasks  # noqa: F401 — registrations
    import app.workers.orchestrator  # noqa: F401 — registrations
    from app.workers.orchestrator import SyncSession, _dispatch_figures
    from app.models.book import Book as BookModel
    import asyncio

    def _do_dispatch() -> str | None:
        with SyncSession() as s:
            b = s.get(BookModel, book_id)
            if b is None:
                return None
            before = b.figures_status
            _dispatch_figures(s, b)
            s.refresh(b)
            if b.figures_status != "running" or before == "running":
                # CAS lost — nothing dispatched.
                return None
            # Get the freshly-created Job ID.
            from app.models.job import Job as JobModel
            from sqlalchemy import select as _select
            j = s.execute(
                _select(JobModel)
                .where(JobModel.book_id == book_id, JobModel.type == "extract_figures")
                .order_by(JobModel.id.desc())
                .limit(1)
            ).scalars().first()
            return str(j.id) if j else None

    job_id_str = await asyncio.to_thread(_do_dispatch)
    if job_id_str is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                "Figures extraction already running or terminal — "
                "refused duplicate dispatch."
            ),
        )

    return {
        "book_id": str(book.id),
        "job_id": job_id_str,
        "status": "queued",
    }


# ---------------------------------------------------------------------------
# GET /api/books/{book_id}/figures
# ---------------------------------------------------------------------------

@books_router.post("/{book_id}/re-embed-figures")
async def re_embed_figures(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Re-run the figure embedder for this book.

    Useful when figures are extracted but their FigureReference rows are
    missing (extractor crashed mid-flight before embedder ran, or
    section_id ended up as '_orphan' so the embedder skipped them).
    Idempotent — wipes existing refs for this book and rewrites them.
    """
    from app.services.figure_embedder import embed_figures_for_book_sync
    from app.core.config import settings
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    def _run():
        engine = create_engine(settings.SYNC_DATABASE_URL, pool_pre_ping=True, future=True)
        Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
        with Session() as sync_session:
            counters = embed_figures_for_book_sync(sync_session, book_id)
            sync_session.commit()
            return counters

    # Run sync embedder in a thread (async route, sync ORM internals).
    import asyncio
    counters = await asyncio.to_thread(_run)
    return {"ok": True, "counters": counters}


@books_router.get("/{book_id}/figures")
async def list_book_figures(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """List figures grouped by section_ref. Only sections with >=1 figure
    appear (Q4 — UI filter). Each section carries an aggregated context
    tag from its figures' references.
    """
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")

    figs = (
        await session.execute(
            select(Figure)
            .where(Figure.book_id == book_id)
            .order_by(Figure.section_id, Figure.page_number, Figure.figure_id_text)
        )
    ).scalars().all()
    if not figs:
        return {"book_id": str(book_id), "sections": [], "total_figures": 0}

    refs = (
        await session.execute(
            select(FigureReference).where(FigureReference.book_id == book_id)
        )
    ).scalars().all()
    refs_by_fig: dict[UUID, list[FigureReference]] = {}
    for r in refs:
        refs_by_fig.setdefault(r.figure_id, []).append(r)

    # Group by section. Use the Figure.section_id as primary anchor; if
    # the same image appears with multiple refs across sections, the
    # references[] array carries them all.
    sections: dict[str, dict[str, Any]] = {}
    for f in figs:
        sref = f.section_id or "_orphan"
        s = sections.setdefault(sref, {
            "section_ref": sref,
            "figures": [],
            "contexts": set(),
            "n_theory": 0,
            "n_question": 0,
            "n_regen": 0,        # 0016 — has any regen variant
            "n_approved": 0,     # 0016 — approved into Regenerated folder
        })
        fig_refs = refs_by_fig.get(f.id, [])
        s["figures"].append(_figure_dict(f, refs=fig_refs))
        if f.regen_image_bytes:
            s["n_regen"] += 1
        if f.approved_at is not None:
            s["n_approved"] += 1
        for r in fig_refs:
            s["contexts"].add(r.context)
            if r.context == "theory":
                s["n_theory"] += 1
            elif r.context == "question":
                s["n_question"] += 1

    # Lookup section titles from the book's schema so the response
    # carries human-readable names alongside the id ref. Reviewers need
    # to confirm "Fig 4.7 belongs to Quadratic Equations" without
    # cross-referencing the sidebar.
    title_by_id: dict[str, str] = {}
    if book and book.schema:
        try:
            from app.schemas.analyser import BookSchema
            from app.services.chunk_builder import flatten_sections as _flatten
            schema_obj = BookSchema(**book.schema)
            for ss in _flatten(schema_obj):
                title_by_id[ss.id] = ss.title
        except Exception:
            pass

    out_sections = []
    for sref, s in sections.items():
        out_sections.append({
            "section_ref": sref,
            "section_title": title_by_id.get(sref),
            "figures": s["figures"],
            "contexts": sorted(s["contexts"]),
            "n_theory": s["n_theory"],
            "n_question": s["n_question"],
            "n_regen": s["n_regen"],
            "n_approved": s["n_approved"],
        })
    out_sections.sort(key=lambda s: s["section_ref"])

    return {
        "book_id": str(book_id),
        "sections": out_sections,
        "total_figures": len(figs),
    }


# ---------------------------------------------------------------------------
# GET /api/figures/{figure_id}
# ---------------------------------------------------------------------------

@figures_router.get("/{figure_id}")
async def get_figure(
    figure_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    fig = await session.get(Figure, figure_id)
    if fig is None:
        raise HTTPException(404, detail="Figure not found")
    refs = (
        await session.execute(
            select(FigureReference).where(FigureReference.figure_id == figure_id)
        )
    ).scalars().all()
    return _figure_dict(fig, refs=list(refs))


# ---------------------------------------------------------------------------
# GET /api/figures/{figure_id}/image?variant=original|regenerated|auto
# ---------------------------------------------------------------------------

@figures_router.get("/{figure_id}/image")
async def get_figure_image(
    figure_id: UUID,
    variant: str = Query("auto", pattern="^(original|regenerated|auto)$"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Stream image bytes for a figure.

    variant=original    → image_bytes
    variant=regenerated → regen_image_bytes (404 if missing)
    variant=auto        → regen_image_bytes if present, else image_bytes (Q7)
    """
    fig = await session.get(Figure, figure_id)
    if fig is None:
        raise HTTPException(404, detail="Figure not found")

    if variant == "regenerated":
        data = fig.regen_image_bytes
    elif variant == "original":
        data = fig.image_bytes
    else:  # auto
        data = fig.regen_image_bytes or fig.image_bytes

    if not data:
        raise HTTPException(
            404,
            detail=f"No {variant} image bytes available for this figure",
        )
    mime = fig.mime_type or "image/png"
    return Response(content=data, media_type=mime)


# ---------------------------------------------------------------------------
# POST /api/books/{book_id}/sections/{section_ref}/regenerate-figures
# ---------------------------------------------------------------------------

class RegenFiguresRequest(BaseModel):
    style: str = Field(default="enhanced", pattern="^(enhanced|original)$")
    custom_instructions: str | None = None
    # Default OFF: Gemini's safety filter silently rejects watermark-removal
    # requests (100% empty responses observed). The v2 pipeline regenerates a
    # fresh image with no watermark anyway, so the stage is redundant. Opt-in
    # only.
    watermark_clean: bool = False
    # Default OFF so reviewers see the raw Gemini regen (the overlay step
    # repaints original labels onto the regen image which made regen
    # visually identical to the original — defeating the purpose).
    overlay: bool = False
    image_model: str | None = None
    ocr_model: str | None = None


@books_router.post(
    "/{book_id}/sections/{section_ref}/regenerate-figures",
    status_code=status.HTTP_201_CREATED,
)
async def regenerate_figures_section(
    book_id: UUID,
    section_ref: str,
    payload: RegenFiguresRequest = Body(default_factory=RegenFiguresRequest),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Section-scoped regen — regenerates every figure in the section."""
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")

    # Confirm at least one figure exists in this section before queuing
    figs = (
        await session.execute(
            select(Figure)
            .where(Figure.book_id == book_id)
            .where(Figure.section_id == section_ref)
            .limit(1)
        )
    ).scalars().first()
    if figs is None:
        raise HTTPException(
            404,
            detail=f"No figures in section {section_ref!r} for this book",
        )

    job = Job(
        book_id=book.id,
        type="regenerate_figures_v2_section",
        status="queued",
        progress=0,
    )
    session.add(job)
    await session.flush()
    await session.commit()

    import app.workers.figures_tasks  # noqa: F401
    from app.workers.runner import dispatch

    params_json = payload.model_dump_json()
    dispatch(
        "regenerate_figures_v2_section",
        str(book.id),
        section_ref,
        params_json,
        str(job.id),
    )
    return {
        "book_id": str(book.id),
        "section_ref": section_ref,
        "job_id": str(job.id),
        "status": "queued",
        "params": payload.model_dump(),
    }


# ---------------------------------------------------------------------------
# POST /api/figures/{figure_id}/discard-regen
# ---------------------------------------------------------------------------

@figures_router.post("/{figure_id}/discard-regen")
async def discard_figure_regen(
    figure_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    fig = await session.get(Figure, figure_id)
    if fig is None:
        raise HTTPException(404, detail="Figure not found")
    fig.regen_image_bytes = None
    fig.regen_status = "none"
    fig.regen_version = 0
    fig.regen_cache_key = None
    if fig.regen_meta is None:
        fig.regen_meta = {}
    fig.regen_meta = {**fig.regen_meta, "discarded": True}
    await session.commit()
    return {
        "figure_id": str(figure_id),
        "status": "discarded",
    }


class FigureDiagramRegenRequest(BaseModel):
    """Body for the manual figure regeneration.

    ``engine`` selects the regeneration engine; ``auto`` picks by the figure's
    semantic_type (table → vector grid + embedded graphic; diagram/chart →
    LaTeX/SVG vector; illustration/photo → image-model redraw).
    """
    custom_instructions: str | None = Field(default=None, max_length=2000)
    engine: str = Field(default="auto", pattern="^(auto|vector|table_embed|image)$")


@figures_router.post("/{figure_id}/regenerate-diagram")
async def regenerate_figure_diagram(
    figure_id: UUID,
    payload: FigureDiagramRegenRequest = Body(default_factory=FigureDiagramRegenRequest),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Manually regenerate a figure with engine routing. On-demand only — the
    figure is never auto-updated here. On success the rasterized/regenerated PNG
    is stored as the figure's approved regen variant, so Preview/Composer/Export
    reflect it. ``engine=auto`` (default) mirrors the automatic pipeline routing.
    """
    fig = await session.get(Figure, figure_id)
    if fig is None:
        raise HTTPException(404, detail="Figure not found")
    if not settings.MULTIMODAL_REGEN_ENABLED:
        raise HTTPException(400, detail="Multimodal diagram regen is disabled")
    if not fig.image_bytes:
        raise HTTPException(400, detail="Figure has no source image to regenerate")

    from app.workers.question_regen_v3 import (
        pick_regen_engine,
        regenerate_table_figure,
        regenerate_theory_figure,
    )

    engine = payload.engine
    if engine == "auto":
        engine = pick_regen_engine(fig)

    if engine == "table_embed":
        result = await asyncio.to_thread(
            regenerate_table_figure, figure_id, payload.custom_instructions
        )
    elif engine == "image":
        from app.workers.figures_tasks import redraw_single_figure

        result = await asyncio.to_thread(
            redraw_single_figure,
            figure_id,
            style="enhanced",
            custom_instructions=payload.custom_instructions,
        )
    else:  # vector
        result = await asyncio.to_thread(
            regenerate_theory_figure, figure_id, payload.custom_instructions
        )
    if isinstance(result, dict):
        result.setdefault("engine", engine)
    err = result.get("_error") if isinstance(result, dict) else "failed"
    if err == "fallback":
        # The model judged the figure too complex to vectorize — original kept.
        return {
            "ok": False,
            "fallback": True,
            "figure_id": str(figure_id),
            "message": result.get("description")
            or "Diagram too complex to vectorize — original figure kept.",
        }
    if err == "no_image":
        raise HTTPException(400, detail="Figure has no source image to regenerate")
    if not result.get("ok"):
        raise HTTPException(500, detail="Figure diagram regeneration failed")
    return result


class FigureRedrawRequest(BaseModel):
    """Body for the on-demand single-figure 'Redraw cleanly' (image model)."""
    style: str = Field(default="enhanced", pattern="^(enhanced|original)$")
    custom_instructions: str | None = Field(default=None, max_length=2000)
    watermark_clean: bool = False
    overlay: bool = False


@figures_router.post("/{figure_id}/redraw")
async def redraw_figure(
    figure_id: UUID,
    payload: FigureRedrawRequest = Body(default_factory=FigureRedrawRequest),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """On-demand 'Redraw cleanly' for ONE figure — the image-model redraw
    (same engine as the section batch), scoped to a single figure and
    auto-approved so it shows immediately. Distinct from the LaTeX/SVG
    /regenerate-diagram path; this one is a clean raster redraw.
    """
    fig = await session.get(Figure, figure_id)
    if fig is None:
        raise HTTPException(404, detail="Figure not found")
    if not fig.image_bytes:
        raise HTTPException(400, detail="Figure has no source image to redraw")

    from app.workers.figures_tasks import redraw_single_figure

    result = await asyncio.to_thread(
        redraw_single_figure,
        figure_id,
        style=payload.style,
        custom_instructions=payload.custom_instructions,
        watermark_clean=payload.watermark_clean,
        overlay=payload.overlay,
    )
    err = result.get("_error") if isinstance(result, dict) else "failed"
    if err == "no_image":
        raise HTTPException(400, detail="Figure has no source image to redraw")
    if not result.get("ok"):
        raise HTTPException(500, detail="Figure redraw failed")
    return result


# ---------------------------------------------------------------------------
# 0016 — Approval workflow (Q5 — "Approve & move to Regenerated" folder)
# ---------------------------------------------------------------------------

@books_router.post(
    "/{book_id}/sections/{section_ref}/figures/approve",
    status_code=status.HTTP_200_OK,
)
async def approve_section_figures(
    book_id: UUID,
    section_ref: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Mark every figure in this section that has a regen variant as
    APPROVED. Stamps `approved_at` so the ✨ Regenerated sidebar folder
    starts surfacing them. Only figures with `regen_image_bytes` are
    eligible — originals without a variant stay un-approved (since
    "Regenerated folder" by definition holds regen variants).
    """
    from datetime import datetime

    rows = (
        await session.execute(
            select(Figure)
            .where(Figure.book_id == book_id)
            .where(Figure.section_id == section_ref)
        )
    ).scalars().all()
    if not rows:
        raise HTTPException(404, detail=f"No figures in {section_ref!r}")
    now = datetime.utcnow()
    approved = 0
    skipped = 0
    for f in rows:
        if f.regen_image_bytes:
            f.approved_at = now
            approved += 1
        else:
            skipped += 1
    await session.commit()
    # Re-run figure embedder — approval flips which variant is surfaced
    # by the renderer. Non-fatal.
    try:
        from app.services.figure_embedder import embed_figures_for_book
        await embed_figures_for_book(session, book_id)
        await session.commit()
    except Exception as e:
        logger.warning("figure_embedder after approve failed: %s", e)
    return {
        "section_ref": section_ref,
        "approved": approved,
        "skipped_without_regen": skipped,
        "approved_at": now.isoformat(),
    }


@books_router.post(
    "/{book_id}/sections/{section_ref}/figures/unapprove",
    status_code=status.HTTP_200_OK,
)
async def unapprove_section_figures(
    book_id: UUID,
    section_ref: str,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Reverse of approve — clears `approved_at` for every figure in this
    section. They drop out of the ✨ Regenerated folder back into draft."""
    rows = (
        await session.execute(
            select(Figure)
            .where(Figure.book_id == book_id)
            .where(Figure.section_id == section_ref)
        )
    ).scalars().all()
    cleared = 0
    for f in rows:
        if f.approved_at is not None:
            f.approved_at = None
            cleared += 1
    await session.commit()
    try:
        from app.services.figure_embedder import embed_figures_for_book
        await embed_figures_for_book(session, book_id)
        await session.commit()
    except Exception as e:
        logger.warning("figure_embedder after unapprove failed: %s", e)
    return {"section_ref": section_ref, "unapproved": cleared}


@figures_router.post("/{figure_id}/approve", status_code=status.HTTP_200_OK)
async def approve_one_figure(
    figure_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Per-figure approval — same effect as section approve, just one row.
    Useful for the per-card 'keep regen' button."""
    from datetime import datetime

    f = await session.get(Figure, figure_id)
    if f is None:
        raise HTTPException(404, detail="Figure not found")
    if not f.regen_image_bytes:
        raise HTTPException(
            400,
            detail="Figure has no regen variant to approve",
        )
    f.approved_at = datetime.utcnow()
    await session.commit()
    try:
        from app.services.figure_embedder import embed_figures_for_book
        await embed_figures_for_book(session, f.book_id)
        await session.commit()
    except Exception as e:
        logger.warning("figure_embedder after one-approve failed: %s", e)
    return {
        "figure_id": str(figure_id),
        "approved_at": f.approved_at.isoformat(),
    }


@figures_router.post("/{figure_id}/unapprove", status_code=status.HTTP_200_OK)
async def unapprove_one_figure(
    figure_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Per-figure unapprove."""
    f = await session.get(Figure, figure_id)
    if f is None:
        raise HTTPException(404, detail="Figure not found")
    f.approved_at = None
    await session.commit()
    try:
        from app.services.figure_embedder import embed_figures_for_book
        await embed_figures_for_book(session, f.book_id)
        await session.commit()
    except Exception as e:
        logger.warning("figure_embedder after one-unapprove failed: %s", e)
    return {"figure_id": str(figure_id), "status": "unapproved"}


# ---------------------------------------------------------------------------
# Per-ref hide / unhide — Phase 1, point 3.
# When the user clicks ✕ on an embedded figure in Theory or Questions, we
# flip is_hidden on that figure_reference row. Hidden refs are excluded
# from all render responses and from final-merge exports. The Figure
# itself is untouched — only this placement is suppressed.
# ---------------------------------------------------------------------------

@books_router.post("/figure-references/{ref_id}/hide", status_code=status.HTTP_200_OK)
async def hide_figure_reference(
    ref_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    ref = await session.get(FigureReference, ref_id)
    if ref is None:
        raise HTTPException(404, detail="FigureReference not found")
    ref.is_hidden = True
    await session.commit()
    return {"ref_id": str(ref_id), "is_hidden": True}


@books_router.post("/figure-references/{ref_id}/unhide", status_code=status.HTTP_200_OK)
async def unhide_figure_reference(
    ref_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    ref = await session.get(FigureReference, ref_id)
    if ref is None:
        raise HTTPException(404, detail="FigureReference not found")
    ref.is_hidden = False
    await session.commit()
    return {"ref_id": str(ref_id), "is_hidden": False}


# Hard-delete a figure reference. Used by the unattached panel's Delete CTA
# when the user has decided this figure doesn't belong anywhere. The Figure
# row itself is untouched — only the placement is removed. A subsequent
# embedder run can recreate the reference if the figure still matches.
@books_router.delete(
    "/figure-references/{ref_id}", status_code=status.HTTP_200_OK
)
async def delete_figure_reference(
    ref_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    ref = await session.get(FigureReference, ref_id)
    if ref is None:
        raise HTTPException(404, detail="FigureReference not found")
    await session.delete(ref)
    await session.commit()
    return {"ref_id": str(ref_id), "deleted": True}


# Re-run the figure embedder on demand. Used after manual schema edits or
# when the user wants to retry placement (e.g. after adding labels). The
# embedder is idempotent and deterministic — safe to call any time.
@books_router.post("/{book_id}/reembed-figures", status_code=status.HTTP_200_OK)
async def reembed_figures(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    from app.services.figure_embedder import embed_figures_for_book
    counters = await embed_figures_for_book(session, book_id)
    return {"book_id": str(book_id), "counters": counters}


# ---------------------------------------------------------------------------
# GET /api/books/{book_id}/unattached-figures
# Figures the embedder couldn't place (no theory match + no question
# target). User reviews these in a dedicated tray; nothing is auto-dumped
# into a random section. Phase 1, point 2.
# ---------------------------------------------------------------------------

@books_router.get("/{book_id}/unattached-figures")
async def list_unattached_figures(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    refs = (
        await session.execute(
            select(FigureReference)
            .where(FigureReference.book_id == book_id)
            .where(FigureReference.placement_kind == "unattached")
            .where(FigureReference.is_hidden.is_(False))
        )
    ).scalars().all()
    if not refs:
        return {"book_id": str(book_id), "figures": []}
    fig_ids = {r.figure_id for r in refs}
    figs = (
        await session.execute(
            select(Figure).where(Figure.id.in_(fig_ids))
        )
    ).scalars().all()
    fig_by_id = {f.id: f for f in figs}
    out = []
    for r in refs:
        f = fig_by_id.get(r.figure_id)
        if f is None:
            continue
        variant = "regen" if (f.regen_image_bytes and f.approved_at) else "original"
        out.append({
            "ref_id": str(r.id),
            "figure_id": str(f.id),
            "label": f.figure_number or r.placeholder_text or "",
            "caption": f.caption or "",
            "variant": variant,
            "image_url": f"/api/figures/{f.id}/image?variant=auto",
            "context": r.context,
            "section_ref": r.section_ref,
            "page_number": f.page_number,
        })
    return {"book_id": str(book_id), "figures": out}


# ---------------------------------------------------------------------------
# GET /api/books/{book_id}/figure-references
# ---------------------------------------------------------------------------

@books_router.get("/{book_id}/figure-references")
async def list_figure_references(
    book_id: UUID,
    section_ref: str | None = Query(default=None),
    context: str | None = Query(default=None, pattern="^(theory|question)$"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    q = select(FigureReference).where(FigureReference.book_id == book_id)
    if section_ref is not None:
        q = q.where(FigureReference.section_ref == section_ref)
    if context is not None:
        q = q.where(FigureReference.context == context)
    rows = (await session.execute(q)).scalars().all()
    return {
        "book_id": str(book_id),
        "section_ref": section_ref,
        "context": context,
        "references": [_ref_dict(r) for r in rows],
    }


# ---------------------------------------------------------------------------
# GET /api/books/{book_id}/figure-regenerations
#   List of regen attempts grouped by section and timestamp (a "run"). One
#   row per figure per regen attempt — mirrors the Questions / Theory regen
#   folder pattern at the UI level. Image bytes for historical runs are NOT
#   recoverable (Figure.regen_image_bytes is latest-only); use this endpoint
#   for run history, params, status, timestamps.
# ---------------------------------------------------------------------------

def _regen_row_dict(r: FigureRegeneration) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "book_id": str(r.book_id),
        "figure_id": str(r.figure_id),
        "section_id": r.section_id,
        "image_url": r.image_url,
        "style_params": r.style_params,
        "model_used": r.model_used,
        "status": r.status,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@books_router.get("/{book_id}/figure-regenerations")
async def list_figure_regenerations(
    book_id: UUID,
    section_ref: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """List figure regen attempts for a book.

    Each row is one figure × one regen attempt. The UI groups them into
    "runs" by (section_id, ~created_at) so users see their regen history
    similar to Questions / Theory regen folders.

    Returns rows ordered by created_at desc (most recent first).
    """
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")
    q = (
        select(FigureRegeneration)
        .where(FigureRegeneration.book_id == book_id)
        .order_by(FigureRegeneration.created_at.desc())
    )
    if section_ref is not None:
        q = q.where(FigureRegeneration.section_id == section_ref)
    rows = (await session.execute(q)).scalars().all()

    # Group rows into "runs" — same section + close created_at (within 60s).
    # This mirrors how a regen job produces N rows ~simultaneously.
    runs: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for r in rows:
        row_d = _regen_row_dict(r)
        if current is not None:
            same_section = current["section_id"] == r.section_id
            try:
                last_ts = current["_last_ts"]
                gap = abs((r.created_at - last_ts).total_seconds()) if r.created_at and last_ts else 999
            except Exception:
                gap = 999
            if same_section and gap <= 60:
                current["rows"].append(row_d)
                current["_last_ts"] = r.created_at
                continue
        # New run cluster
        current = {
            "section_id": r.section_id,
            "started_at": row_d["created_at"],
            "rows": [row_d],
            "_last_ts": r.created_at,
        }
        runs.append(current)

    # Strip the private cursor field before returning
    for run in runs:
        run.pop("_last_ts", None)
        # Summary stats per run for UI cards
        rs = run["rows"]
        run["total"] = len(rs)
        run["succeeded"] = sum(1 for x in rs if x["status"] == "ready")
        run["failed"] = sum(1 for x in rs if x["status"] == "failed")
        # Model + first non-empty style_params for at-a-glance display
        run["model_used"] = next((x["model_used"] for x in rs if x["model_used"]), None)
        run["style_params"] = next((x["style_params"] for x in rs if x["style_params"]), None)

    return {
        "book_id": str(book_id),
        "section_ref": section_ref,
        "total_attempts": len(rows),
        "runs": runs,
    }
