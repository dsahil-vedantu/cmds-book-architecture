"""Final Merge API — Phase 2.

Endpoints:
  GET    /api/books/{book_id}/final-merge                 — view JSON
  GET    /api/books/{book_id}/final-merge/export/json     — JSON download
  GET    /api/books/{book_id}/final-merge/export/markdown — Markdown download
  GET    /api/books/{book_id}/final-merge/export/docx     — DOCX download

The view endpoint returns the same JSON the page renders so frontend +
export share one source of truth.
"""

from __future__ import annotations

import io
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.services.final_merge import build_final_merge

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/books", tags=["final-merge"])


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------

@router.get("/{book_id}/final-merge")
async def get_final_merge(
    book_id: UUID,
    prefer_regen: bool = Query(default=True, description=(
        "When true, regenerated theory/question/figure variants override "
        "originals. When false, returns the pure original extraction."
    )),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        return await build_final_merge(session, book_id, prefer_regen=prefer_regen)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------

def _safe_name(s: str) -> str:
    return re.sub(r"[^\w-]+", "_", s or "book").strip("_") or "book"


def _ensure_pandoc_on_path() -> str:
    """Locate the pandoc binary for pypandoc (mirrors banks exporter)."""
    found = shutil.which("pandoc")
    if found:
        return found
    candidates = [
        Path.home() / ".local/bin/pandoc",
        Path("/opt/homebrew/bin/pandoc"),
        Path("/usr/local/bin/pandoc"),
    ]
    for p in candidates:
        if p.exists() and p.is_file():
            return str(p)
    raise HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="pandoc binary not found — install pandoc to enable DOCX export",
    )


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def _block_to_md(block: dict[str, Any]) -> str:
    """Render one theory block as Markdown."""
    t = block.get("t")
    c = block.get("c") or ""
    if t == "p":
        return c
    if t == "h3":
        return f"### {c}"
    if t == "eq":
        return f"$${c}$$"
    if t == "def":
        term = block.get("term") or ""
        return f"**Definition — {term}**\n\n{c}"
    if t == "kp":
        return f"> **Key Point**\n>\n> {c}"
    if t == "list":
        items = block.get("items") or []
        # Strip pre-numbered prefixes so the MD ordered list is clean.
        strip_re = re.compile(r"^\s*(?:\(\d+\)|\d+[.)])\s+")
        lines = []
        for i, it in enumerate(items):
            clean = strip_re.sub("", str(it))
            lines.append(f"{i+1}. {clean}")
        return "\n".join(lines)
    if t == "table":
        headers = block.get("headers") or []
        rows = block.get("rows") or []
        caption = block.get("caption") or ""
        parts: list[str] = []
        if caption:
            parts.append(f"*{caption}*")
        if headers:
            parts.append("| " + " | ".join(headers) + " |")
            parts.append("|" + "|".join(["---"] * len(headers)) + "|")
        for row in rows:
            parts.append("| " + " | ".join(str(x) for x in row) + " |")
        return "\n".join(parts)
    if t == "example":
        label = block.get("label") or "Example"
        prob = block.get("prob") or ""
        eqs = block.get("eqs") or []
        parts = [f"**{label}**"]
        if prob:
            parts.append(prob)
        for e in eqs:
            parts.append(f"$${e}$$")
        return "\n\n".join(parts)
    if t == "fig":
        label = block.get("label") or ""
        return f"*{label}* {c}".strip()
    if t in ("example_ref", "exercise_ref", "question_ref"):
        # Render the chip label verbatim — no "Worked example: / Exercise: /
        # Question:" prefix. The label is already the verbatim section title
        # (e.g. "Self Test 1 (5 Q)", "Illustration 1", "EXAMPLE 9.1") set
        # by example_linker._label_for, and prepending a generic kind
        # defeats that intent + diverges from TheoryView rendering, which
        # also drops the prefix.
        return f"→ *{block.get('label') or block.get('number') or ''}*"
    return c or ""


def _figure_to_md(fig: dict[str, Any], abs_base: str) -> str:
    """Render a figure as a Markdown image with caption."""
    label = fig.get("label") or "figure"
    caption = fig.get("caption") or ""
    img_url = fig.get("image_url") or ""
    if img_url and not img_url.startswith("http"):
        img_url = abs_base.rstrip("/") + img_url
    alt = label or caption or "figure"
    parts = [f"![{alt}]({img_url})"]
    if caption:
        parts.append(f"*{caption}*")
    return "\n\n".join(parts)


def _question_to_md(q: dict[str, Any], abs_base: str) -> str:
    """Render one question (raw_text, embedded figures, solution) as MD."""
    parts: list[str] = []
    header_bits = []
    if q.get("question_number"):
        header_bits.append(f"Q{q['question_number']}")
    if q.get("page_start"):
        header_bits.append(f"p.{q['page_start']}")
    if q.get("question_type"):
        header_bits.append(q["question_type"])
    if header_bits:
        parts.append("**" + " · ".join(header_bits) + "**")
    if q.get("raw_text"):
        parts.append(q["raw_text"])
    for fig in q.get("embedded_figures") or []:
        parts.append(_figure_to_md(fig, abs_base))
    if q.get("has_solution") and q.get("solution_text"):
        parts.append(f"**Solution**\n\n{q['solution_text']}")
    return "\n\n".join(parts)


def _build_markdown(doc: dict[str, Any], abs_base: str) -> str:
    """Compose the full document as Markdown."""
    book = doc.get("book") or {}
    parts: list[str] = []
    title = book.get("title") or "Book"
    parts.append(f"# {title}")
    if book.get("subject"):
        parts.append(f"*{book['subject']}*")

    for sec in doc.get("sections") or []:
        # Heading level capped at h6
        level = max(1, min(6, int(sec.get("level") or 2) + 1))
        parts.append(f"{'#' * level} {sec.get('section_title') or sec.get('section_id')}")

        # Splice theory figures inline between blocks
        figs_by_idx: dict[int, list[dict]] = {}
        trailing_figs: list[dict] = []
        for f in sec.get("embedded_figures") or []:
            idx = f.get("placement_block_idx")
            if idx is None:
                trailing_figs.append(f)
            else:
                figs_by_idx.setdefault(int(idx), []).append(f)

        for i, b in enumerate(sec.get("blocks") or []):
            parts.append(_block_to_md(b))
            for f in figs_by_idx.get(i, []):
                parts.append(_figure_to_md(f, abs_base))
        for f in trailing_figs:
            parts.append(_figure_to_md(f, abs_base))

        # Questions
        questions = sec.get("questions") or []
        if questions:
            parts.append("---")
            parts.append("**Questions**")
            for q in questions:
                parts.append(_question_to_md(q, abs_base))

    if doc.get("unattached_figures"):
        parts.append("---")
        parts.append("## Unattached Figures (not auto-placed)")
        for f in doc["unattached_figures"]:
            parts.append(_figure_to_md(f, abs_base))

    return "\n\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# Export endpoints
# ---------------------------------------------------------------------------

@router.get("/{book_id}/final-merge/export/json")
async def export_final_merge_json(
    book_id: UUID,
    prefer_regen: bool = Query(default=True),
    session: AsyncSession = Depends(get_session),
) -> Response:
    try:
        doc = await build_final_merge(session, book_id, prefer_regen=prefer_regen)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))
    name = _safe_name(doc.get("book", {}).get("title", "")) + "_final-merge"
    return Response(
        content=json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8"),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}.json"'},
    )


@router.get("/{book_id}/final-merge/export/markdown")
async def export_final_merge_markdown(
    book_id: UUID,
    prefer_regen: bool = Query(default=True),
    session: AsyncSession = Depends(get_session),
) -> Response:
    try:
        doc = await build_final_merge(session, book_id, prefer_regen=prefer_regen)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))
    # For MD export, image paths must be absolute (downloaders read them
    # outside the browser session).
    from app.core.config import settings
    abs_base = getattr(settings, "public_api_base", "") or ""
    md = _build_markdown(doc, abs_base)
    name = _safe_name(doc.get("book", {}).get("title", "")) + "_final-merge"
    return Response(
        content=md.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}.md"'},
    )


@router.get("/{book_id}/final-merge/export/docx")
async def export_final_merge_docx(
    book_id: UUID,
    prefer_regen: bool = Query(default=True),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """DOCX export of the Final Merge view.

    Routes through the SAME `_DocBuilder` polished pipeline as the
    Composer's draft export. Both produce visually identical output
    (NAVY headings, native tables, embedded image binaries, MCQ option
    splits, Unicode math). This replaces the old pandoc path which
    produced a different look.
    """
    try:
        doc = await build_final_merge(session, book_id, prefer_regen=prefer_regen)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(e))

    # Convert the merge document into the same item-list shape the
    # Composer's draft uses, so we can reuse build_final_draft_docx.
    items: list[dict[str, Any]] = []
    for sec in doc.get("sections", []):
        items.append({
            "type": "section_heading",
            "title": sec.get("section_title") or sec.get("section_id") or "",
            "section_id": sec.get("section_id"),
            "level": sec.get("level", 0),
            "regen": sec.get("block_source") == "regen",
        })
        # Interleave blocks with embedded figures (inline at block idx)
        # and inlined questions (anchored to block idx).
        blocks = sec.get("blocks") or []
        figs = sec.get("embedded_figures") or []
        figs_by_idx: dict[int, list[dict[str, Any]]] = {}
        trailing_figs: list[dict[str, Any]] = []
        for f in figs:
            idx = f.get("placement_block_idx")
            if idx is None:
                trailing_figs.append(f)
            else:
                figs_by_idx.setdefault(int(idx), []).append(f)
        inlined = sec.get("inlined_questions_by_block_idx") or {}
        # Inlined questions at anchor "-1" (before any block)
        for q in inlined.get("-1", []):
            items.append({"type": "question", "question": q})
        for i, b in enumerate(blocks):
            items.append({"type": "block", "block": b})
            for fig in figs_by_idx.get(i, []):
                items.append({"type": "figure", "figure": fig})
            for q in inlined.get(str(i), []):
                items.append({"type": "question", "question": q})
        for f in trailing_figs:
            items.append({"type": "figure", "figure": f})
        for q in sec.get("questions") or []:
            items.append({"type": "question", "question": q})

    # Pull figure binaries (variant = regen if approved, else original)
    fig_ids: set[str] = set()
    for it in items:
        t = it.get("type")
        if t == "figure":
            fid = (it.get("figure") or {}).get("figure_id")
            if fid:
                fig_ids.add(str(fid))
        elif t == "question":
            for f in (it.get("question") or {}).get("embedded_figures") or []:
                fid = f.get("figure_id")
                if fid:
                    fig_ids.add(str(fid))
    figure_bytes_map: dict[str, bytes] = {}
    if fig_ids:
        from app.models.figure import Figure
        from sqlalchemy import select
        rows = (
            await session.execute(
                select(Figure).where(Figure.id.in_([UUID(x) for x in fig_ids]))
            )
        ).scalars().all()
        for row in rows:
            data = (
                row.regen_image_bytes
                if (row.regen_image_bytes and row.approved_at is not None)
                else row.image_bytes
            )
            if data:
                figure_bytes_map[str(row.id)] = data

    from app.services.docx_export import build_final_draft_docx
    title = doc.get("book", {}).get("title", "") or "Final"
    try:
        bytes_out = build_final_draft_docx(title, items, figure_bytes_map)
    except Exception as e:
        logger.exception("Final merge DOCX render failed for book %s", book_id)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"DOCX render failed: {e}",
        )

    name = _safe_name(title) + "_final-merge"
    return Response(
        content=bytes_out,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        headers={"Content-Disposition": f'attachment; filename="{name}.docx"'},
    )
