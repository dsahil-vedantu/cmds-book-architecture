"""Question regenerations router.

POST   /api/question-banks/{bank_id}/regenerate                       — start a regen run
GET    /api/books/{book_id}/question-regenerations                    — list runs for a book
GET    /api/question-regenerations/{regen_id}                         — fetch one run
GET    /api/question-regenerations/{regen_id}/questions               — list regen questions grouped by section
POST   /api/question-regenerations/{regen_id}/save                    — mark run as saved
DELETE /api/question-regenerations/{regen_id}                         — delete the run + its questions
DELETE /api/question-regenerations/{regen_id}/questions               — bulk-delete questions inside a run
POST   /api/question-regenerations/{regen_id}/retry-section           — re-run regen for ONE section (R6)
GET    /api/question-regenerations/{regen_id}/export/json             — download JSON (R10)
GET    /api/question-regenerations/{regen_id}/export/markdown         — download Markdown (R10)
GET    /api/question-regenerations/{regen_id}/export/docx             — download Word document (R10)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.rate_limit import extraction_limit
from app.models.book import Book
from app.models.job import Job
from app.models.question import Question
from app.models.question_bank import QuestionBank
from app.models.question_regeneration import QuestionRegeneration

books_router = APIRouter(prefix="/api/books", tags=["question-regenerations"])
banks_router = APIRouter(prefix="/api/question-banks", tags=["question-regenerations"])
regens_router = APIRouter(prefix="/api/question-regenerations", tags=["question-regenerations"])


class RegenerateRequest(BaseModel):
    scope: str = Field(default="bank", pattern="^(bank|sections)$")
    section_refs: list[str] | None = None
    custom_instructions: str | None = None
    source_regen_id: UUID | None = None
    label: str | None = None

    # R4 — v3 regen params. All optional with worker-side defaults.
    similarity_level: Literal[
        "numbers_only",
        "numbers_and_rephrase",
        "numbers_rephrase_add_concept",
        "new_question_same_topic",
        "same_topic_add_one_concept",
        "same_chapter_any_topic",
    ] | None = Field(default=None)
    question_type: str | None = Field(default=None, max_length=64)
    priority_mode: Literal["override"] = "override"


def _regen_dict(r: QuestionRegeneration, question_count: int = 0) -> dict:
    return {
        "id": str(r.id),
        "bank_id": str(r.bank_id),
        "book_id": str(r.book_id),
        "source_regen_id": str(r.source_regen_id) if r.source_regen_id else None,
        "label": r.label,
        "scope": r.scope,
        "section_refs": list(r.section_refs or []),
        "custom_instructions": r.custom_instructions,
        # R9 — surface the v3 params so the regen review page can show them
        # in the "Parameters used for this run" card. Without these, the UI
        # rendered all dashes even when the DB had values.
        "similarity_level": getattr(r, "similarity_level", None),
        "count": getattr(r, "count", None),
        "question_type": getattr(r, "question_type", None),
        "priority_mode": getattr(r, "priority_mode", None),
        "status": r.status,
        "job_id": str(r.job_id) if r.job_id else None,
        "question_count": question_count,
        "stats": r.extraction_stats,
        "last_error": r.last_error,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }


def _question_dict(
    q: Question,
    figs_by_qid: dict[str, list[dict]] | None = None,
) -> dict:
    """Serialize a question (source OR regen variant).

    ``figs_by_qid`` is the canonical question-figure map
    ({question_id_str: [figure_dict]}) from
    figure_serializer.serialize_embedded_figures(context="question").
    Figure resolution:
      • a SOURCE/original question → its own figures (by q.id)
      • a REGEN VARIANT → NO inherited figure. A regenerated question has new
        values, so the source's original figure would be misleading. The
        variant shows its regenerated LaTeX/SVG diagram (below) or, if that's
        absent/failed, no figure at all — never the stale original.
    Each figure_dict carries body_target so the frontend renders it
    under the question stem vs inside the solution block — identical to
    the extracted-content question view.
    """
    embedded: list[dict] = []
    if figs_by_qid is not None:
        embedded = figs_by_qid.get(str(q.id)) or []
    # Step 2 (chained diagram regen) — surface the LaTeX/SVG payload + the
    # image-regen hint stored in qc_local so the Regenerated tab can render a
    # live vector preview. Present only for image-bearing regen variants.
    image_regen_hint = None
    regenerated_diagram = None
    if isinstance(q.qc_local, dict):
        ir = q.qc_local.get("image_regen")
        if isinstance(ir, dict) and ir.get("needed"):
            image_regen_hint = {"needed": True, "reason": ir.get("reason") or ""}
        rd = q.qc_local.get("regenerated_diagram")
        if isinstance(rd, dict):
            regenerated_diagram = {
                "fallback_to_original": bool(rd.get("fallback_to_original", False)),
                "subject": rd.get("subject") or "",
                "latex_code": rd.get("latex_code") or "",
                "svg_preview": rd.get("svg_preview") or "",
                "description": rd.get("description") or "",
            }
    return {
        "id": str(q.id),
        "regen_id": str(q.regen_id) if q.regen_id else None,
        "source_question_id": (
            str(q.source_question_id) if q.source_question_id else None
        ),
        "section_ref": q.section_ref,
        "section_title": q.section_title,
        "page_start": q.page_start,
        "page_end": q.page_end,
        "raw_text": q.raw_text,
        "status": q.status,
        "excluded_block_ref": q.excluded_block_ref,
        "excluded_block_index": q.excluded_block_index,
        "link_method": q.link_method,
        "link_confidence": q.link_confidence,
        "question_number": q.question_number,
        "exercise_ref": q.exercise_ref,
        "chapter_ref": q.chapter_ref,
        "sub_part": q.sub_part,
        "question_type": q.question_type,
        "has_options": q.has_options,
        "solution_text": q.solution_text,
        "has_solution": q.has_solution,
        "kind": q.kind or "exercise",
        "embedded_figures": embedded,
        # qc_local carries the no-skip fallback flag (regen_failed) so the
        # frontend can badge a retained-original variant ("couldn't
        # regenerate — original retained") and offer a retry.
        "qc_local": q.qc_local,
        # Step 2 — the regenerated diagram payload + image-needed hint, so the
        # Regenerated tab shows the new diagram (or a "diagram unavailable" note).
        "image_regen_hint": image_regen_hint,
        "regenerated_diagram": regenerated_diagram,
    }


@banks_router.post(
    "/{bank_id}/regenerate",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(extraction_limit)],
)
async def start_regeneration(
    bank_id: UUID,
    payload: RegenerateRequest = Body(default_factory=RegenerateRequest),
    session: AsyncSession = Depends(get_session),
) -> dict:
    bank = await session.get(QuestionBank, bank_id)
    if bank is None:
        raise HTTPException(404, detail="Bank not found")
    book = await session.get(Book, bank.book_id)
    if book is None or not book.pdf_url or not book.schema:
        raise HTTPException(400, detail="Book is not analysed/uploaded")

    if payload.scope == "sections" and not payload.section_refs:
        raise HTTPException(400, detail="section_refs required when scope='sections'")

    job = Job(book_id=book.id, type="extract_questions_regen_v3", status="queued", progress=0)
    session.add(job)
    await session.flush()

    regen = QuestionRegeneration(
        bank_id=bank.id,
        book_id=book.id,
        source_regen_id=payload.source_regen_id,
        label=payload.label,
        scope=payload.scope,
        section_refs=payload.section_refs,
        custom_instructions=(payload.custom_instructions or None),
        # R4 — v3 regen params (all optional; worker uses defaults if None)
        similarity_level=payload.similarity_level,
        question_type=payload.question_type,
        priority_mode=payload.priority_mode,
        status="pending",
        job_id=job.id,
    )
    session.add(regen)
    await session.commit()

    # R4 — dispatch the v3 task (was: extract_questions_regen / v2).
    # The v2 task is left registered as a fallback but no longer wired here.
    import app.workers.question_regen_v3  # noqa: F401
    from app.workers.runner import dispatch

    dispatch("extract_questions_regen_v3", str(regen.id), str(job.id))

    return {
        "regen_id": str(regen.id),
        "job_id": str(job.id),
        "status": "extracting",
    }


@books_router.get("/{book_id}/question-regenerations")
async def list_regenerations(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    # R6 — Only return regens whose parent bank still exists. Historically
    # FK cascade was off in SQLite so deleting a bank left orphan regen
    # rows behind; on re-extract a fresh bank was created and the UI listed
    # the stale regens, which then produced "No source questions to
    # regenerate" on retrigger. Drop them at read time so the UI is clean,
    # and physically delete the orphans (idempotent housekeeping).
    live_bank_ids = (
        await session.execute(
            select(QuestionBank.id).where(QuestionBank.book_id == book_id)
        )
    ).scalars().all()
    live_bank_set = set(live_bank_ids)
    rows = (
        await session.execute(
            select(QuestionRegeneration)
            .where(QuestionRegeneration.book_id == book_id)
            .order_by(QuestionRegeneration.created_at.desc())
        )
    ).scalars().all()
    orphans = [r for r in rows if r.bank_id not in live_bank_set]
    if orphans:
        for o in orphans:
            await session.delete(o)
        await session.commit()
        rows = [r for r in rows if r.bank_id in live_bank_set]

    counts_rows = await session.execute(
        select(Question.regen_id, func.count(Question.id))
        .where(Question.book_id == book_id)
        .where(Question.regen_id.is_not(None))
        .group_by(Question.regen_id)
    )
    counts = {rid: n for rid, n in counts_rows.all()}

    return [_regen_dict(r, counts.get(r.id, 0)) for r in rows]


@regens_router.get("/{regen_id}")
async def get_regeneration(
    regen_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    r = await session.get(QuestionRegeneration, regen_id)
    if r is None:
        raise HTTPException(404, detail="Regeneration not found")
    count_row = await session.execute(
        select(func.count(Question.id)).where(Question.regen_id == regen_id)
    )
    return _regen_dict(r, int(count_row.scalar() or 0))


@regens_router.get("/{regen_id}/questions")
async def list_regen_questions(
    regen_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    r = await session.get(QuestionRegeneration, regen_id)
    if r is None:
        raise HTTPException(404, detail="Regeneration not found")

    # Canonical question-figure map for the book. Regen variants inherit
    # their source question's figures (the embedder runs only on the
    # original extraction, so variants have no figure_references of their
    # own). _question_dict resolves own-then-source per question. Identical
    # serializer the extract-content question view uses → figures render
    # the same in the regen review, including body_target (question vs
    # solution) routing.
    from app.services.figure_serializer import serialize_embedded_figures
    figs_by_qid = await serialize_embedded_figures(
        session, r.book_id, context="question", variant="auto",
    )

    # Regen questions (rows with regen_id set on this run).
    # Skip hidden ones — ✕ button on a regen variant must remove it from view.
    rows = (
        await session.execute(
            select(Question)
            .where(Question.regen_id == regen_id)
            .where(Question.is_hidden.is_(False))
            .order_by(
                Question.section_ref.nulls_last(),
                Question.source_question_id.nulls_last(),
                Question.page_start.nulls_last(),
                Question.id,
            )
        )
    ).scalars().all()

    # Pre-fetch each source Question once so the response carries the full
    # source text alongside its variants (UI groups source ↔ variants).
    source_ids = sorted({
        q.source_question_id for q in rows
        if q.source_question_id is not None
    })
    source_map: dict[UUID, Question] = {}
    if source_ids:
        srows = (
            await session.execute(
                select(Question).where(Question.id.in_(source_ids))
            )
        ).scalars().all()
        source_map = {s.id: s for s in srows}

    grouped: dict[str, dict[str, Any]] = {}
    for q in rows:
        key = q.section_ref or "_unsectioned"
        bucket = grouped.setdefault(
            key,
            {
                "section_ref": q.section_ref,
                "section_title": q.section_title,
                # Flat list — preserved for backward compat with older
                # frontend code that consumes `sections[].questions[]`.
                "questions": [],
                # 0014 — variants grouped by source_question_id. Each
                # entry: {source_id, source: <Question|null>, variants: [...]}
                # Variants without a source_question_id (old runs) go into
                # the "_orphan" group at the end.
                "sources": [],
            },
        )
        bucket["questions"].append(_question_dict(q, figs_by_qid))

    # Second pass to assemble the `sources` groups deterministically.
    for sec in grouped.values():
        per_source: dict[str, dict[str, Any]] = {}
        orphan_variants: list[dict[str, Any]] = []
        for qd in sec["questions"]:
            sid = qd.get("source_question_id")
            if not sid:
                orphan_variants.append(qd)
                continue
            sid_str = str(sid)
            if sid_str not in per_source:
                src_q = source_map.get(UUID(sid_str)) if isinstance(sid_str, str) else None
                per_source[sid_str] = {
                    "source_id": sid_str,
                    "source": (
                        _question_dict(src_q, figs_by_qid)
                        if src_q is not None else None
                    ),
                    "variants": [],
                }
            per_source[sid_str]["variants"].append(qd)
        sec["sources"] = list(per_source.values())
        if orphan_variants:
            sec["sources"].append({
                "source_id": None,
                "source": None,
                "variants": orphan_variants,
            })

    return {
        "regen": _regen_dict(r, len(rows)),
        "sections": list(grouped.values()),
    }


@regens_router.post("/{regen_id}/save")
async def save_regeneration(
    regen_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    r = await session.get(QuestionRegeneration, regen_id)
    if r is None:
        raise HTTPException(404, detail="Regeneration not found")
    # R2 — accept both "ready" (all sections complete) and "partial" (some
    # sections failed/skipped). The user explicitly wants partial runs
    # saveable so they can keep what worked. "saved" is also idempotent.
    if r.status not in {"ready", "partial", "saved"}:
        raise HTTPException(400, detail=f"Cannot save regen with status={r.status}")
    r.status = "saved"
    await session.commit()
    # The UPDATE fires `updated_at`'s server-side onupdate, which SQLAlchemy
    # expires on commit. `_regen_dict` is synchronous and reads `r.updated_at`
    # — a lazy refresh from a sync attribute access inside an async request
    # raises MissingGreenlet (→ 500 on the "Save Questions Regen" CTA).
    # Refresh explicitly in the async context so every column is loaded.
    await session.refresh(r)
    return _regen_dict(r)


class RetrySectionRequest(BaseModel):
    """R6 — body for section-level regen retry.

    section_ref is passed in body (not path) because it may contain "::"
    separators or other characters that complicate URL encoding.

    custom_instructions (optional) — section-level user instruction layered
    on top of the regen's original params. When present, the question
    regenerator merges it into its prompt for THIS section only.
    """
    section_ref: str = Field(min_length=1, max_length=255)
    custom_instructions: str | None = None


@regens_router.post("/{regen_id}/retry-section")
async def retry_regen_section(
    regen_id: UUID,
    payload: RetrySectionRequest = Body(...),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """R6 — re-run regeneration for ONE section within an existing regen.

    Wipes existing regen Question rows for (regen_id, section_ref) and
    dispatches a new job that processes only that section. Other sections
    in the regen are preserved. Per-section status in
    `regen.extraction_stats.sections` is updated; totals recomputed.
    """
    r = await session.get(QuestionRegeneration, regen_id)
    if r is None:
        raise HTTPException(404, detail="Regeneration not found")
    if r.status not in {"ready", "partial", "failed", "saved", "extracting"}:
        raise HTTPException(
            400, detail=f"Cannot retry section on regen with status={r.status}",
        )

    job = Job(
        book_id=r.book_id,
        type="retry_regen_section_v3",
        status="queued",
        progress=0,
    )
    session.add(job)
    await session.flush()
    await session.commit()

    import app.workers.question_regen_v3  # noqa: F401
    from app.workers.runner import dispatch

    # Section-level custom_instructions: pass to worker as 4th positional
    # arg. Worker layers it on top of regen.custom_instructions for THIS
    # retry only (does NOT mutate the persisted regen record).
    section_custom = (payload.custom_instructions or "").strip() or None

    dispatch(
        "retry_regen_section_v3",
        str(r.id),
        payload.section_ref,
        str(job.id),
        section_custom,
    )

    return {
        "regen_id": str(r.id),
        "section_ref": payload.section_ref,
        "job_id": str(job.id),
        "status": "queued",
    }


@regens_router.delete("/{regen_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_regeneration(
    regen_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> None:
    r = await session.get(QuestionRegeneration, regen_id)
    if r is None:
        return None
    await session.delete(r)
    await session.commit()
    return None


class BulkDeleteRequest(BaseModel):
    question_ids: list[UUID]


@regens_router.delete("/{regen_id}/questions", status_code=status.HTTP_200_OK)
async def bulk_delete_regen_questions(
    regen_id: UUID,
    payload: BulkDeleteRequest = Body(...),
    session: AsyncSession = Depends(get_session),
) -> dict:
    r = await session.get(QuestionRegeneration, regen_id)
    if r is None:
        raise HTTPException(404, detail="Regeneration not found")
    if not payload.question_ids:
        return {"deleted": 0}
    rows = (
        await session.execute(
            select(Question)
            .where(Question.regen_id == regen_id)
            .where(Question.id.in_(payload.question_ids))
        )
    ).scalars().all()
    for q in rows:
        await session.delete(q)
    await session.commit()
    return {"deleted": len(rows)}


# ===========================================================================
# R10 — Exports (JSON, Markdown, DOCX), overall + per-section
# ===========================================================================

_FIG_PLACEHOLDER_RE = re.compile(r"\{\{\s*fig\s*:\s*([^}]+?)\s*\}\}", re.IGNORECASE)


def _safe_name(s: str) -> str:
    """Filesystem-safe filename component."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", (s or "regen").strip())
    return s.strip("_") or "regen"


def _render_figure_placeholders(text: str) -> str:
    """Mirror of bank exporter — convert {{fig: ...}} → italic callout."""
    def _sub(m: re.Match[str]) -> str:
        inner = (m.group(1) or "").strip() or "(unlabelled figure)"
        return f"*[Figure: {inner}]*"
    return _FIG_PLACEHOLDER_RE.sub(_sub, text or "")


async def _grouped_regen_questions(
    regen_id: UUID,
    section_ref: str | None,
    session: AsyncSession,
) -> tuple[QuestionRegeneration, list[dict[str, Any]]]:
    """Load regen + group its questions by section. If section_ref is given,
    return only that section's group.
    """
    r = await session.get(QuestionRegeneration, regen_id)
    if r is None:
        raise HTTPException(404, detail="Regeneration not found")
    q = (
        select(Question)
        .where(Question.regen_id == regen_id)
        .order_by(
            Question.section_ref.nulls_last(),
            Question.page_start.nulls_last(),
            Question.id,
        )
    )
    if section_ref is not None:
        q = q.where(Question.section_ref == section_ref)
    rows = (await session.execute(q)).scalars().all()
    grouped: dict[str, dict[str, Any]] = {}
    for qq in rows:
        key = qq.section_ref or "_unsectioned"
        bucket = grouped.setdefault(key, {
            "section_ref": qq.section_ref,
            "section_title": qq.section_title,
            "questions": [],
        })
        bucket["questions"].append(_question_dict(qq))
    return r, list(grouped.values())


def _build_regen_markdown(
    regen: QuestionRegeneration,
    sections: list[dict[str, Any]],
    section_only: str | None = None,
) -> str:
    """Render a regen run as GFM. LaTeX math preserved in $...$/$$...$$ so
    pandoc emits OMML equations. Figure placeholders surface as inline
    italic callouts.
    """
    title = (regen.label or f"Regeneration {str(regen.id)[:8]}")
    suffix = f" — {section_only}" if section_only else ""
    lines: list[str] = [f"# {title}{suffix} (Regenerated Questions)", ""]
    if regen.custom_instructions:
        lines.append("**Custom instructions:** " + regen.custom_instructions)
        lines.append("")
    for sec in sections:
        qs = sec["questions"]
        if not qs:
            continue
        title_line = (
            f"## {sec['section_ref']} {sec.get('section_title') or ''}".rstrip()
        )
        lines.append(title_line)
        lines.append("")
        for idx, qd in enumerate(qs, start=1):
            body = _render_figure_placeholders(qd.get("raw_text") or "")
            lines.append(f"{idx}. {body}")
            sol = _render_figure_placeholders(qd.get("solution_text") or "")
            if sol:
                lines.append("")
                lines.append(f"    **Answer.** {sol}")
            lines.append("")
    return "\n".join(lines)


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
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            os.environ["PATH"] = f"{c.parent}:{os.environ.get('PATH', '')}"
            os.environ.setdefault("PYPANDOC_PANDOC", str(c))
            return str(c)
    raise HTTPException(
        500,
        detail="pandoc binary not found — install pandoc to enable DOCX export",
    )


@regens_router.get("/{regen_id}/export/json")
async def export_regen_json(
    regen_id: UUID,
    section_ref: str | None = Query(default=None, max_length=255),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Download JSON. ?section_ref=... limits to one section."""
    regen, sections = await _grouped_regen_questions(regen_id, section_ref, session)
    payload = {
        "regen": _regen_dict(regen, sum(len(s["questions"]) for s in sections)),
        "sections": sections,
    }
    name = _safe_name(regen.label or f"regen_{str(regen.id)[:8]}")
    if section_ref:
        name += f"__{_safe_name(section_ref)}"
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}.json"'},
    )


@regens_router.get("/{regen_id}/export/markdown")
async def export_regen_markdown(
    regen_id: UUID,
    section_ref: str | None = Query(default=None, max_length=255),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Download Markdown. ?section_ref=... limits to one section."""
    regen, sections = await _grouped_regen_questions(regen_id, section_ref, session)
    md = _build_regen_markdown(regen, sections, section_only=section_ref)
    name = _safe_name(regen.label or f"regen_{str(regen.id)[:8]}")
    if section_ref:
        name += f"__{_safe_name(section_ref)}"
    return Response(
        content=md.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}.md"'},
    )


@regens_router.get("/{regen_id}/export/docx")
async def export_regen_docx(
    regen_id: UUID,
    section_ref: str | None = Query(default=None, max_length=255),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Download Word .docx via the native python-docx builder.
    `?section_ref=...` limits to one section."""
    from app.services.docx_export import build_regen_docx

    regen, sections = await _grouped_regen_questions(regen_id, section_ref, session)
    label_text = regen.label or f"Regeneration {str(regen.id)[:8]}"
    data = build_regen_docx(
        label_text,
        regen.custom_instructions,
        sections,
        section_only=section_ref,
    )
    name = _safe_name(regen.label or f"regen_{str(regen.id)[:8]}")
    if section_ref:
        name += f"__{_safe_name(section_ref)}"
    return Response(
        content=data,
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        headers={"Content-Disposition": f'attachment; filename="{name}.docx"'},
    )

