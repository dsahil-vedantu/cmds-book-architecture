"""Folders router — list, create, get-one, delete.

Folders are purely organisational metadata for V-Studio. The endpoints
here do not touch the extraction / regeneration pipelines: they read the
existing ``books`` rows to compute aggregate counts and let the user
group those rows under a folder name.

Delete is blocked while a folder still contains books — this is safer
than cascade-deleting actual content. The user has to move or remove
books first.
"""

from __future__ import annotations

import random
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models.book import Book
from app.models.figure import Figure
from app.models.folder import Folder
from app.schemas.folder import FolderCreate, FolderOut


router = APIRouter(prefix="/api/folders", tags=["folders"])


# Palette mirrors the design language (Vedantu navy + supporting hues).
# A fresh folder gets a random color from this list unless the caller
# overrides via FolderCreate.color.
#
# 16 distinct hues, each with a matching gradient pair on the frontend
# (frontend-v2/src/components/FolderCard.tsx). When adding a new color
# here, also add the gradient mapping over there.
PALETTE = [
    # Originals
    "#1A237E",  # indigo / brand
    "#E94B35",  # red-orange / brand accent
    "#10B981",  # green
    "#8B5CF6",  # violet
    "#F59E0B",  # amber
    "#0E7C6B",  # teal
    "#3B82F6",  # blue
    "#EC4899",  # pink
    # Alternates
    "#06B6D4",  # cyan
    "#F43F5E",  # rose
    "#84CC16",  # lime
    "#D946EF",  # magenta / fuchsia
    "#FB923C",  # orange / sunset
    "#64748B",  # slate / neutral
    "#EAB308",  # mustard
    "#A78471",  # mocha / brown
]


def _pick_color() -> str:
    return random.choice(PALETTE)


async def _hydrate_counts(session: AsyncSession, folder: Folder) -> FolderOut:
    """Attach aggregate counts to a Folder row.

    Three lightweight queries: book count + book status breakdown +
    figure count. Question count is summed from each book's schema JSON
    (``expected_question_count``); doing that as SQL across dialects is
    fiddly so we walk the rows in Python — folder sizes will stay in the
    tens, so this is fine.
    """
    # All books in this folder.
    rows = (
        await session.execute(
            select(Book.id, Book.status, Book.schema).where(Book.folder_id == folder.id)
        )
    ).all()

    chapters = len(rows)
    ready = sum(1 for _, s, _ in rows if s in {"extracted", "approved", "done", "ready"})
    processing = sum(
        1
        for _, s, _ in rows
        if s in {"analysing", "extracting", "re_extracting", "regenerating"}
    )
    queued = chapters - ready - processing

    questions = 0
    for _, _, schema in rows:
        if not schema:
            continue
        # schema is a JSON dict; walk sections + subsections summing
        # expected_question_count where present.
        stack = list(schema.get("sections", []) or [])
        while stack:
            sec = stack.pop()
            try:
                questions += int(sec.get("expected_question_count", 0) or 0)
            except (TypeError, ValueError):
                pass
            subs = sec.get("subsections") or []
            if subs:
                stack.extend(subs)

    book_ids = [bid for bid, _, _ in rows]
    if book_ids:
        figures = (
            await session.execute(
                select(func.count(Figure.id)).where(Figure.book_id.in_(book_ids))
            )
        ).scalar_one()
    else:
        figures = 0

    return FolderOut(
        id=folder.id,
        name=folder.name,
        color=folder.color,
        subject=folder.subject,
        created_at=folder.created_at,
        updated_at=folder.updated_at,
        chapters=chapters,
        questions=questions,
        figures=int(figures or 0),
        chapters_ready=ready,
        chapters_processing=processing,
        chapters_queued=queued,
    )


@router.get("", response_model=list[FolderOut])
async def list_folders(session: AsyncSession = Depends(get_session)) -> list[FolderOut]:
    folders = (
        await session.execute(select(Folder).order_by(Folder.created_at.asc()))
    ).scalars().all()
    return [await _hydrate_counts(session, f) for f in folders]


@router.post("", response_model=FolderOut, status_code=status.HTTP_201_CREATED)
async def create_folder(
    body: FolderCreate, session: AsyncSession = Depends(get_session)
) -> FolderOut:
    name = body.name.strip()
    if not name:
        raise HTTPException(400, detail="Folder name is required.")
    folder = Folder(
        name=name,
        color=body.color or _pick_color(),
        subject=body.subject,
    )
    session.add(folder)
    await session.flush()
    return await _hydrate_counts(session, folder)


@router.get("/{folder_id}", response_model=FolderOut)
async def get_folder(
    folder_id: UUID, session: AsyncSession = Depends(get_session)
) -> FolderOut:
    folder = await session.get(Folder, folder_id)
    if folder is None:
        raise HTTPException(404, detail="Folder not found")
    return await _hydrate_counts(session, folder)


@router.delete("/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    folder_id: UUID, session: AsyncSession = Depends(get_session)
) -> None:
    folder = await session.get(Folder, folder_id)
    if folder is None:
        raise HTTPException(404, detail="Folder not found")

    # Block delete if folder is non-empty — safer than cascade-deleting books.
    book_count = (
        await session.execute(
            select(func.count(Book.id)).where(Book.folder_id == folder.id)
        )
    ).scalar_one()
    if book_count and int(book_count) > 0:
        raise HTTPException(
            409,
            detail=(
                f"Folder is not empty ({int(book_count)} chapter(s) inside). "
                "Move or remove the chapters first."
            ),
        )
    await session.delete(folder)
