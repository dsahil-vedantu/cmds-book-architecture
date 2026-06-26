"""Book verification — surfaces what's missing vs what schema promised.

Per CONTRACT.md §3 (Verification Contract): every stage must verify
content actually exists before claiming "done". This service runs
read-only audits and returns a structured report.

Used by:
  * /api/books/{id}/quality (Phase 5c) — UI surfaces a quality report
  * Future writers (Phase 5e) — each stage's task calls verify_X()
    before flipping per-stage status to "done" vs "partial"

The report shape is intentionally stable so the UI can render it
consistently. Adding new fields is safe; removing is a breaking
change for the quality endpoint.

The verify_book() function does NOT mutate. It reads sections, questions,
figures, and the schema, computes what should be there vs what is, and
returns a report. Writers in Phase 5e will use the same logic to decide
status transitions.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.book import Book
from app.models.figure import Figure
from app.models.question import Question
from app.models.section import Section


def _flatten_schema_sections(schema: dict | None) -> list[dict]:
    """Walk the schema tree and return every section as a flat list."""
    if not schema:
        return []
    out: list[dict] = []

    def walk(secs):
        for s in secs or []:
            out.append(s)
            walk(s.get("subsections") or [])

    walk(schema.get("sections") or [])
    return out


def _expected_kinds(schema_sec: dict) -> set[str]:
    """Normalize content_types into a set: theory, questions, or both."""
    raw = schema_sec.get("content_types") or []
    if isinstance(raw, str):
        raw = [raw]
    return {str(x).lower() for x in raw}


async def verify_book(
    session: AsyncSession,
    book_id: UUID,
) -> dict[str, Any]:
    """Return a structured quality report.

    Shape:
        {
          "book_id": "<uuid>",
          "schema": {
            "exists": bool,
            "total_sections": int,
            "theory_sections": int,
            "question_sections": int,
            "status": "done" | "failed" | "pending",
          },
          "theory": {
            "expected_sections": int,
            "with_blocks": int,
            "empty_sections": [ {id, title}, ... ],
            "status": "done" | "partial" | "pending",
          },
          "questions": {
            "expected_sections": int,
            "with_questions": int,
            "empty_sections": [ {id, title}, ... ],
            "total_questions": int,
            "status": "done" | "partial" | "pending",
          },
          "figures": {
            "total": int,
            "attached_to_section": int,
            "attached_to_question": int,
            "unattached": int,
            "status": "done" | "partial" | "pending",
          },
          "derived_status": "ready" | "partial" | "failed" | "processing" | "queued",
          "summary": "<human readable>"
        }
    """
    book = await session.get(Book, book_id)
    if not book:
        return {"error": "book_not_found", "book_id": str(book_id)}

    report: dict[str, Any] = {"book_id": str(book_id), "title": book.title}

    # ─── Schema ─────────────────────────────────────────────────
    schema_sections = _flatten_schema_sections(book.schema)
    theory_schema_sections = [
        s for s in schema_sections if "theory" in _expected_kinds(s)
    ]
    question_schema_sections = [
        s for s in schema_sections if "questions" in _expected_kinds(s)
    ]
    schema_status = (
        "done" if schema_sections
        else ("failed" if book.schema is not None else "pending")
    )
    report["schema"] = {
        "exists": bool(book.schema),
        "total_sections": len(schema_sections),
        "theory_sections": len(theory_schema_sections),
        "question_sections": len(question_schema_sections),
        "status": schema_status,
    }

    # ─── Sections (theory blocks) ─────────────────────────────────
    sec_rows = (
        await session.execute(
            select(Section).where(Section.book_id == book_id)
        )
    ).scalars().all()
    sec_by_slug = {s.section_id: s for s in sec_rows}
    sec_by_title = {(s.title or "").strip().lower(): s for s in sec_rows}

    theory_empty: list[dict] = []
    theory_with = 0
    for ss in theory_schema_sections:
        # Try slug match, then title fallback (mirrors Phase 3 final_merge logic)
        row = sec_by_slug.get(ss.get("id"))
        if row is None:
            row = sec_by_title.get((ss.get("title") or "").strip().lower())
        if row and (row.blocks or []):
            theory_with += 1
        else:
            theory_empty.append({
                "id": ss.get("id"),
                "title": ss.get("title"),
                "page_start": ss.get("page_start"),
                "page_end": ss.get("page_end"),
            })

    theory_total = len(theory_schema_sections)
    # Phase 5e respect: if a stage is currently running, don't label it
    # failed/partial from content counts (stages mid-flight haven't
    # finished writing yet). "pending" means worker hasn't started.
    if book.theory_status == "running":
        theory_status = "running"
    elif book.theory_status == "pending" and theory_with == 0:
        theory_status = "pending"
    elif theory_total == 0:
        theory_status = "done" if book.schema else "pending"
    elif theory_with == theory_total:
        theory_status = "done"
    elif theory_with == 0:
        theory_status = "failed"
    else:
        theory_status = "partial"
    report["theory"] = {
        "expected_sections": theory_total,
        "with_blocks": theory_with,
        "empty_sections": theory_empty,
        "status": theory_status,
    }

    # ─── Questions ────────────────────────────────────────────────
    q_rows = (
        await session.execute(
            select(Question).where(Question.book_id == book_id)
        )
    ).scalars().all()

    # Group questions by section — prefer FK (section_uuid), fall back to section_ref
    qs_by_section_uuid: dict[Any, int] = {}
    qs_by_slug: dict[str, int] = {}
    for q in q_rows:
        if q.section_uuid is not None:
            qs_by_section_uuid[q.section_uuid] = (
                qs_by_section_uuid.get(q.section_uuid, 0) + 1
            )
        elif q.section_ref:
            qs_by_slug[q.section_ref] = qs_by_slug.get(q.section_ref, 0) + 1

    q_empty: list[dict] = []
    q_with = 0
    for ss in question_schema_sections:
        ss_slug = ss.get("id") or ""
        ss_title = (ss.get("title") or "").strip().lower()
        # Match by slug → resolve to UUID via sec_by_slug; or title fallback
        row = sec_by_slug.get(ss_slug) or sec_by_title.get(ss_title)
        count = 0
        if row and row.id in qs_by_section_uuid:
            count = qs_by_section_uuid[row.id]
        if count == 0:
            count = qs_by_slug.get(ss_slug, 0)
        if count > 0:
            q_with += 1
        else:
            q_empty.append({
                "id": ss.get("id"),
                "title": ss.get("title"),
                "expected": ss.get("expected_question_count"),
            })

    q_total_schema = len(question_schema_sections)
    # Phase 5e respect: running stages aren't failed; "pending" means
    # the worker hasn't started yet (so 0 content is expected, not failed).
    if book.questions_status == "running":
        q_status = "running"
    elif book.questions_status == "pending" and q_with == 0:
        q_status = "pending"
    elif q_total_schema == 0:
        q_status = "done" if book.schema else "pending"
    elif q_with == q_total_schema:
        q_status = "done"
    elif q_with == 0:
        q_status = "failed"
    else:
        q_status = "partial"

    report["questions"] = {
        "expected_sections": q_total_schema,
        "with_questions": q_with,
        "empty_sections": q_empty,
        "total_questions": len(q_rows),
        "status": q_status,
    }

    # ─── Figures ──────────────────────────────────────────────────
    fig_rows = (
        await session.execute(
            select(Figure).where(Figure.book_id == book_id)
        )
    ).scalars().all()
    attached_section = sum(1 for f in fig_rows if f.section_uuid is not None)
    # "attached to question" isn't directly on Figure — would need FigureReference;
    # for v1 of this report we treat section-uuid as the indicator.
    total = len(fig_rows)
    unattached = total - attached_section
    if total == 0:
        fig_status = "done" if book.schema else "pending"
    elif unattached == 0:
        fig_status = "done"
    elif attached_section == 0:
        fig_status = "failed"
    else:
        fig_status = "partial"
    report["figures"] = {
        "total": total,
        "attached_to_section": attached_section,
        "unattached": unattached,
        "status": fig_status,
    }

    # ─── Derived book status ──────────────────────────────────────
    # Order matters: failed > running (in-flight) > partial > pending > done.
    # "running" precedes "pending" so a mid-flight book reads as
    # "processing" not "queued".
    statuses = [schema_status, theory_status, q_status, fig_status]
    if "failed" in statuses:
        derived = "failed"
    elif "running" in statuses:
        derived = "processing"
    elif "partial" in statuses:
        derived = "partial"
    elif all(s == "done" for s in statuses):
        derived = "ready"
    elif "pending" in statuses:
        derived = "queued"
    else:
        derived = "processing"
    report["derived_status"] = derived

    # Human summary — be honest about pending/processing books
    parts = []
    if derived == "queued":
        if not book.schema:
            parts.append("schema not yet generated")
        else:
            parts.append("extraction queued")
    elif derived == "processing":
        # More specific: which stage is running?
        running_stages = [
            name for name, st in [
                ("schema", schema_status), ("theory", theory_status),
                ("questions", q_status), ("figures", fig_status),
            ] if st == "running"
        ]
        if running_stages:
            parts.append(f"{', '.join(running_stages)} in progress")
        else:
            parts.append("extraction in progress")
    else:
        if theory_empty:
            parts.append(f"{len(theory_empty)} theory sections empty")
        if q_empty:
            parts.append(f"{len(q_empty)} question sections empty")
        if unattached:
            parts.append(f"{unattached} unattached figures")
        if not parts:
            parts.append("all stages verified complete")
    report["summary"] = "; ".join(parts)
    report["book_status_field"] = book.status  # what book.status currently says
    report["lies"] = (book.status == "ready" and derived != "ready")

    return report
