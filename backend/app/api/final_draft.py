"""Final Draft API — Phase 3.2.

Endpoints (one active draft per book):

  GET    /api/books/{book_id}/final-draft
       Returns the current draft. Auto-seeds from Final Merge on first
       access. Idempotent — repeat calls return the same draft until you
       reseed.

  POST   /api/books/{book_id}/final-draft/reseed
       Discards user edits and seeds a fresh draft from the current
       Final Merge state (regen-preferred by default).

  PATCH  /api/books/{book_id}/final-draft
       Applies a batch of typed operations to the draft items. Body:
       {"operations": [{"op": "reorder", "id": "...", "after_id": "..."}, ...]}

  DELETE /api/books/{book_id}/final-draft
       Removes the draft entirely (next GET will seed again).

The composer auto-saves edits via PATCH. Multiple operations can be batched
in one request for instant-feedback drag/edit sessions.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import re

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models.book import Book
from app.models.final_draft import FinalDraft
from app.services.final_draft import (
    OperationError,
    apply_operation,
    seed_draft_items_from_merge,
)
from app.services.final_draft_export import (
    ExportError,
    build_draft_docx,
    build_draft_json,
    build_draft_markdown,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/books", tags=["final-draft"])


def _draft_to_dict(draft: FinalDraft) -> dict[str, Any]:
    return {
        "id": str(draft.id),
        "book_id": str(draft.book_id),
        "status": draft.status,
        "prefer_regen": bool(draft.prefer_regen),
        "is_dirty": bool(draft.is_dirty),
        "items": draft.items or [],
        "item_count": len(draft.items or []),
        "last_seeded_at": (
            draft.last_seeded_at.isoformat() if draft.last_seeded_at else None
        ),
        "updated_at": draft.updated_at.isoformat() if draft.updated_at else None,
    }


def _source_newer_than_seed(book: Book, draft: FinalDraft) -> bool:
    """True if the book's content changed since the draft was last seeded.

    Signal = ``book.updated_at`` (bumped on every stage transition, so a new
    extraction, regen, or figure re-embed moves it forward) vs the draft's
    ``last_seeded_at``. A draft that was never seeded (None) is treated as
    stale. Timestamps are normalised to aware-UTC before comparison so a
    naive/aware mismatch can never raise in the GET path.
    """
    seeded = draft.last_seeded_at
    if seeded is None:
        return True
    upd = getattr(book, "updated_at", None)
    if upd is None:
        return False
    if seeded.tzinfo is None:
        seeded = seeded.replace(tzinfo=timezone.utc)
    if upd.tzinfo is None:
        upd = upd.replace(tzinfo=timezone.utc)
    return upd > seeded


async def _load_or_seed(
    session: AsyncSession,
    book_id: UUID,
    *,
    prefer_regen: bool = True,
    auto_reseed: bool = True,
) -> FinalDraft:
    """Fetch the draft; seed one if it doesn't exist yet.

    ``auto_reseed=False`` returns the persisted draft AS-IS (no fresh seed).
    The PATCH path uses this: a reseed regenerates every item id, so
    reseeding before applying an op would invalidate the id the client just
    sent (→ "unknown id"). Edits must apply against the exact items the
    client is looking at.
    """
    # Verify book exists (clear 404 instead of FK error later)
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Book not found")

    existing = (
        await session.execute(
            select(FinalDraft).where(FinalDraft.book_id == book_id)
        )
    ).scalars().first()

    # UNCONDITIONAL auto-reseed: every Preview/Composer load triggers a
    # fresh seed from build_final_merge → which itself fires the
    # unconditional auto-heal embedder pass. Net effect: every page
    # load reflects the LATEST DB state — figures, questions, theory
    # blocks, schema edits all materialise without manual refresh.
    #
    # Tradeoff (accepted, explicit user direction): drag-drop reorders
    # / custom_text / edit_item modifications are OVERWRITTEN on the
    # next read. The principle is "freshness > staleness of edits."
    # If preserving user edits across re-seeds becomes a requirement,
    # add an `is_dirty` flag on FinalDraft + skip re-seed when set.
    #
    # Wrapped in try/except so the re-seed NEVER breaks the GET — on
    # any failure we serve whatever items were last persisted. Mirrors
    # the auto-heal failure handling in build_final_merge.
    if existing is not None:
        # SOURCE-AWARE reseed — resolves the freshness-vs-edits tension:
        #   • A CLEAN draft (no manual edits) always reseeds, so new
        #     regen / figures / schema edits surface automatically.
        #   • A DIRTY draft (user edited) is normally preserved so their
        #     reorder/delete/edit isn't overwritten.
        #   • EXCEPTION: if the underlying book data changed since we last
        #     seeded (a new extraction / regen / figure re-embed bumps
        #     book.updated_at past last_seeded_at), the stale edits are
        #     superseded by the new content — so we reseed anyway and clear
        #     the dirty flag. This guarantees the Composer/Preview ALWAYS
        #     reflect the latest correct merge after extraction or regen,
        #     which is the whole point of "perfect as a book".
        source_changed = _source_newer_than_seed(book, existing)
        if auto_reseed and (not existing.is_dirty or source_changed):
            try:
                fresh_items = await seed_draft_items_from_merge(
                    session, book_id, prefer_regen=prefer_regen
                )
                existing.items = fresh_items
                existing.last_seeded_at = datetime.utcnow()
                existing.prefer_regen = prefer_regen
                if source_changed:
                    # New source data supersedes stale edits; resume freshness.
                    existing.is_dirty = False
                await session.commit()
                await session.refresh(existing)
            except Exception as e:
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "auto-reseed failed (book=%s, non-fatal): %s",
                    book_id, e,
                )
                await session.rollback()
        return existing

    items = await seed_draft_items_from_merge(
        session, book_id, prefer_regen=prefer_regen
    )
    draft = FinalDraft(
        book_id=book_id,
        items=items,
        status="draft",
        prefer_regen=prefer_regen,
        last_seeded_at=datetime.utcnow(),
    )
    session.add(draft)
    await session.commit()
    await session.refresh(draft)
    return draft


@router.get("/{book_id}/final-draft")
async def get_final_draft(
    book_id: UUID,
    prefer_regen: bool = Query(default=True),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    draft = await _load_or_seed(session, book_id, prefer_regen=prefer_regen)
    return _draft_to_dict(draft)


@router.post("/{book_id}/final-draft/reseed", status_code=status.HTTP_200_OK)
async def reseed_final_draft(
    book_id: UUID,
    prefer_regen: bool = Query(default=True),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Discard the user's edits and rebuild items from current Final
    Merge. Status flips back to 'draft'."""
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Book not found")

    items = await seed_draft_items_from_merge(
        session, book_id, prefer_regen=prefer_regen
    )
    existing = (
        await session.execute(
            select(FinalDraft).where(FinalDraft.book_id == book_id)
        )
    ).scalars().first()
    if existing is None:
        draft = FinalDraft(
            book_id=book_id,
            items=items,
            status="draft",
            prefer_regen=prefer_regen,
            last_seeded_at=datetime.utcnow(),
        )
        session.add(draft)
    else:
        existing.items = items
        existing.status = "draft"
        existing.prefer_regen = prefer_regen
        existing.last_seeded_at = datetime.utcnow()
        # Explicit reseed/merge-regen: discard edits → draft is clean again,
        # so future GETs resume auto-reseeding for freshness.
        existing.is_dirty = False
        draft = existing
    await session.commit()
    await session.refresh(draft)
    return _draft_to_dict(draft)


@router.patch("/{book_id}/final-draft")
async def patch_final_draft(
    book_id: UUID,
    payload: dict[str, Any] = Body(...),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Apply a batch of operations to the draft. Operations are applied
    in order; the first one that errors aborts the whole batch."""
    operations = payload.get("operations") or []
    if not isinstance(operations, list):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="`operations` must be a list"
        )

    # auto_reseed=False: apply ops on the EXACT persisted items the client is
    # editing. Reseeding here would regenerate item ids and break the id the
    # client just sent ("unknown id").
    draft = await _load_or_seed(session, book_id, auto_reseed=False)
    items = list(draft.items or [])
    for i, op in enumerate(operations):
        if not isinstance(op, dict):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"operation[{i}] must be an object",
            )
        try:
            items = apply_operation(items, op)
        except OperationError as e:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail=f"operation[{i}] {op.get('op')!r}: {e}",
            )

    draft.items = items
    # JSON columns: SQLAlchemy doesn't always auto-detect changes when
    # the assigned value structurally equals the old (or shares refs).
    # flag_modified guarantees the update is persisted.
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(draft, "items")
    # Manual edit → mark dirty so GET stops auto-reseeding over it. The edit
    # now persists and reflects in Preview until an explicit reseed/merge.
    draft.is_dirty = True
    # Reset status if user edits after a previous export
    if draft.status == "exported":
        draft.status = "draft"
    await session.commit()
    await session.refresh(draft)
    return _draft_to_dict(draft)


def _safe_filename(s: str, suffix: str) -> str:
    base = re.sub(r"[^\w-]+", "_", s or "draft").strip("_") or "draft"
    return f"{base}_final-draft.{suffix}"


@router.get("/{book_id}/final-draft/export/json")
async def export_draft_json(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> Response:
    draft = await _load_or_seed(session, book_id)
    book = await session.get(Book, book_id)
    title = (book.title if book else "") or ""
    data = await build_draft_json(draft, title)
    return Response(
        content=data,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_safe_filename(title, "json")}"'
            ),
        },
    )


@router.get("/{book_id}/final-draft/export/markdown")
async def export_draft_markdown(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> Response:
    draft = await _load_or_seed(session, book_id)
    book = await session.get(Book, book_id)
    title = (book.title if book else "") or ""
    data = await build_draft_markdown(session, draft, title)
    return Response(
        content=data,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_safe_filename(title, "md")}"'
            ),
        },
    )


@router.get("/{book_id}/final-draft/export/docx")
async def export_draft_docx(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> Response:
    draft = await _load_or_seed(session, book_id)
    book = await session.get(Book, book_id)
    title = (book.title if book else "") or ""
    try:
        data = await build_draft_docx(session, draft, title)
    except ExportError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    except Exception as e:
        logger.exception("draft DOCX render failed for book %s", book_id)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"DOCX render failed: {type(e).__name__}: {e}",
        )
    return Response(
        content=data,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_safe_filename(title, "docx")}"'
            ),
        },
    )


@router.delete("/{book_id}/final-draft", status_code=status.HTTP_200_OK)
async def delete_final_draft(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    existing = (
        await session.execute(
            select(FinalDraft).where(FinalDraft.book_id == book_id)
        )
    ).scalars().first()
    if existing is None:
        return {"deleted": False, "reason": "no draft existed"}
    await session.delete(existing)
    await session.commit()
    return {"deleted": True}
