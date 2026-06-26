"""Sections router — list per book, get, re-extract."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models.figure import Figure
from app.models.figure_reference import FigureReference
from app.models.job import Job
from app.models.section import Section
from app.schemas.book import BookUploadResponse
from app.schemas.section import SectionOut

router = APIRouter(tags=["sections"])


async def _load_embedded_figures(
    session: AsyncSession,
    book_id: UUID,
    variant: str = "auto",
) -> dict[str, list[dict[str, Any]]]:
    """Build a {section_ref: [figure_dict, ...]} map for theory-context
    figure_references on this book.

    Thin delegate to the canonical serializer (services/figure_serializer.py)
    — there is exactly ONE figure-dict shape + regen-visibility rule shared
    by theory/question extract readers AND theory/question regen readers, so
    figures render identically everywhere. ``variant`` controls which image
    the URL serves: "auto" (regen-if-exists, default — the extract review
    page), "original", or "regenerated" (regen-with-fallback).
    """
    from app.services.figure_serializer import serialize_embedded_figures

    return await serialize_embedded_figures(
        session, book_id, context="theory", variant=variant,  # type: ignore[arg-type]
    )


def _order_sections_by_tree(
    all_sections: list[Section],
    schema_flat: list,
) -> list[Section]:
    """Return every section in correct reading order — robustly.

    The previous approach matched each schema node to a Section row by
    UUID-then-slug and appended UNMATCHED rows at the end in lexicographic
    order. That broke badly because the schema generator and the section
    worker build slugs DIFFERENTLY for nested sections (schema:
    ``6-tidal-volume`` vs DB: ``6-capacities-of-the-lungs-tidal-volume``)
    AND their UUIDs diverge — deep sections never matched and got
    dumped alphabetically at the end → jumbled order.

    This instead derives the tree from section_id slugs themselves
    (which DO form a clean parent→child hierarchy in the DB), and only
    USES the schema for SIBLING ORDER when a node happens to match.
    Every section is placed under its real parent. Nothing is ever
    dumped at the end. No dependency on slug==schema-id or
    uuid==schema-uuid.

    Sibling order at each level:
        1. schema position (if that sibling matches a schema node), else
        2. page_start, else
        3. section_id (stable tiebreak)
    """
    schema_pos: dict[tuple[str, str], int] = {}
    for i, ss in enumerate(schema_flat):
        if getattr(ss, "uuid", None):
            schema_pos[("uuid", str(ss.uuid))] = i
        if getattr(ss, "id", None):
            schema_pos[("slug", ss.id)] = i

    by_sid: dict[str, Section] = {s.section_id: s for s in all_sections}

    def direct_pos(s: Section) -> int | None:
        p = schema_pos.get(("uuid", str(s.id)))
        if p is None:
            p = schema_pos.get(("slug", s.section_id))
        return p

    def parent_sid(sid: str) -> str | None:
        # Parent = the longest OTHER section_id that is a segment-prefix.
        parts = sid.split("-")
        for cut in range(len(parts) - 1, 0, -1):
            cand = "-".join(parts[:cut])
            if cand != sid and cand in by_sid:
                return cand
        return None

    children: dict[str, list[str]] = {sid: [] for sid in by_sid}
    roots: list[str] = []
    for sid in by_sid:
        p = parent_sid(sid)
        if p is not None:
            children[p].append(sid)
        else:
            roots.append(sid)

    _BIG = 10 ** 9

    def sib_key(sid: str) -> tuple:
        s = by_sid[sid]
        dp = direct_pos(s)
        ps = s.page_start if s.page_start is not None else _BIG
        return (dp if dp is not None else _BIG, ps, sid)

    ordered: list[Section] = []

    def dfs(sid: str) -> None:
        ordered.append(by_sid[sid])
        for c in sorted(children[sid], key=sib_key):
            dfs(c)

    for r in sorted(roots, key=sib_key):
        dfs(r)
    return ordered


@router.get("/api/books/{book_id}/sections", response_model=list[SectionOut])
async def list_sections(
    book_id: UUID,
    variant: str = Query(
        "auto", pattern="^(auto|original|regenerated)$",
    ),
    session: AsyncSession = Depends(get_session),
) -> list[SectionOut]:
    """Return the book's sections ordered by the schema's hierarchical
    sequence (pre-order tree walk) — NOT lexicographic section_id.

    Lexicographic sort breaks for any chapter with 2-digit subsection
    numbers (e.g. "8.10" sorts before "8.2"). The Preview/Composer/
    export pipelines already walk the schema tree directly via
    final_merge.py + books.py:_get_export_data, so they show the
    correct order. Until this fix, the sidebar (which calls this
    endpoint) was the only surface using lexicographic order — that's
    why users saw jumbled section ordering in the sidebar while
    Preview rendered correctly.

    Fallback: if the book has no schema or the schema parse fails,
    fall back to lexicographic order so old/broken books still load.
    """
    from app.models.book import Book
    from app.schemas.analyser import BookSchema
    from app.services.chunk_builder import flatten_sections as _flatten

    result = await session.execute(
        select(Section).where(Section.book_id == book_id)
    )
    all_sections = list(result.scalars().all())
    embedded_by_section = await _load_embedded_figures(session, book_id, variant)

    # Robust tree-based ordering. See _order_sections_by_tree docstring
    # for the rationale (replaces the old match-then-lexicographic-tail
    # path that jumbled deep sections whose slugs/UUIDs diverged from
    # the schema).
    schema_flat: list = []
    book = await session.get(Book, book_id)
    if book is not None and book.schema:
        try:
            schema_flat = list(_flatten(BookSchema(**book.schema)))
        except Exception:
            schema_flat = []

    ordered_sections = _order_sections_by_tree(all_sections, schema_flat)

    out: list[SectionOut] = []
    for s in ordered_sections:
        d = SectionOut.model_validate(s)
        d.embedded_figures = embedded_by_section.get(s.section_id, [])
        out.append(d)
    return out


@router.get("/api/sections/{section_id}", response_model=SectionOut)
async def get_section(
    section_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> SectionOut:
    sec = await session.get(Section, section_id)
    if sec is None:
        raise HTTPException(404, detail="Section not found")
    out = SectionOut.model_validate(sec)
    # Phase 1 figure embedder — populate inline figures for this section
    embedded_by_section = await _load_embedded_figures(session, sec.book_id)
    out.embedded_figures = embedded_by_section.get(sec.section_id, [])
    return out


@router.post("/api/sections/{section_id}/re-extract", response_model=BookUploadResponse)
async def re_extract_section(
    section_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> BookUploadResponse:
    sec = await session.get(Section, section_id)
    if sec is None:
        raise HTTPException(404, detail="Section not found")

    job = Job(book_id=sec.book_id, type="re_extract", status="queued", progress=0)
    session.add(job)
    await session.flush()

    # Commit before dispatch so worker thread sees the new Job row.
    await session.commit()

    import app.workers.extract  # noqa: F401
    from app.workers.runner import dispatch

    dispatch("re_extract_section", str(sec.id), str(job.id))
    return BookUploadResponse(book_id=sec.book_id, job_id=job.id, status="re_extracting")
