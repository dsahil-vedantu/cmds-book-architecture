"""Regenerations router — kick off regen and inspect results."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models.book import Book
from app.models.job import Job
from app.models.regeneration import Regeneration
from app.models.section import Section
from app.schemas.book import BookUploadResponse
from app.schemas.regen import RegenerationOut, RegenParams, normalize_legacy_params

router = APIRouter(tags=["regenerations"])


@router.get("/api/recap-rules")
async def list_recap_rules() -> list[dict[str, Any]]:
    """Return the catalog of theory-regen recap rules.

    Frontend renders these as opt-in checkboxes in the Theory regen
    config. Activating any rule requires THEORY_REGEN_PROMPT_VERSION=v3
    on the backend; with v1 the recap_rule_ids field is accepted but
    ignored (v1 prompt has no recap placeholders, and worker pre-loop
    is gated on is_recap_enabled()).
    """
    from app.services.recap_config import RECAP_RULES

    return [
        {
            "id": r["id"],
            "label": r["label"],
            "kind": r["kind"],
            "source_labels": r.get("source_labels", []),
            "source_section_patterns": r.get("source_section_patterns", []),
            "description": r["description"],
        }
        for r in RECAP_RULES
    ]


@router.post("/api/books/{book_id}/regenerate", response_model=BookUploadResponse)
async def regenerate_book(
    book_id: UUID,
    body: dict[str, Any] = Body(...),
    session: AsyncSession = Depends(get_session),
) -> BookUploadResponse:
    """Kick off a regeneration.

    Body shape:
        {...RegenParams fields..., "section_ids": ["1.1", "1.2"] | null}

    If ``section_ids`` is omitted or null, every leaf section of the book
    is regenerated. If provided, only those sections are regenerated — the
    rest are left absent from ``blocks_by_section``.
    """
    book = await session.get(Book, book_id)
    if book is None:
        raise HTTPException(404, detail="Book not found")

    # Split body: RegenParams fields + optional section_ids
    section_ids_raw = body.pop("section_ids", None)
    try:
        params = RegenParams(**body)
    except Exception as e:
        raise HTTPException(422, detail=f"Invalid regen params: {e}") from e

    section_ids: list[str] | None = None
    if section_ids_raw is not None:
        if not isinstance(section_ids_raw, list) or not all(
            isinstance(s, str) for s in section_ids_raw
        ):
            raise HTTPException(422, detail="section_ids must be a list of strings")
        # Empty list = nothing selected → reject (avoids creating empty regen)
        if not section_ids_raw:
            raise HTTPException(400, detail="section_ids is empty — select at least one section")
        section_ids = list(section_ids_raw)

    # Stash the section selection on the regen row so startup recovery
    # after a backend crash can restart with the same scope.
    params_payload = params.model_dump()
    if section_ids is not None:
        params_payload["_section_ids"] = list(section_ids)

    # CARRY-FORWARD — seed the new regen row with the prior regen's
    # blocks_by_section / qc_drift, so sections the user previously
    # regenerated stay visible in the Final / Composer view even when
    # this run's scope only covers a subset of sections. The worker
    # then MERGES this run's regenerated sections into the seed.
    prior = (
        await session.execute(
            select(Regeneration)
            .where(Regeneration.book_id == book.id)
            .order_by(desc(Regeneration.created_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    seed_blocks = dict(prior.blocks_by_section or {}) if prior else {}
    seed_qc = dict(prior.qc_drift or {}) if prior and prior.qc_drift else {}

    regen = Regeneration(
        book_id=book.id,
        params=params_payload,
        blocks_by_section=seed_blocks,
        qc_drift=seed_qc or None,
    )
    session.add(regen)
    await session.flush()

    job = Job(book_id=book.id, type="regen", status="queued", progress=0)
    session.add(job)
    await session.flush()

    # Commit BEFORE dispatch. In inline mode the worker thread starts
    # immediately and runs with a separate sync session — it can't see
    # uncommitted data. Without this commit, regen_row lookup in the
    # worker returns None and blocks_by_section stays empty ({}).
    await session.commit()

    import app.workers.extract  # noqa: F401
    from app.workers.runner import dispatch

    dispatch(
        "regenerate_book",
        str(book.id),
        str(job.id),
        str(regen.id),
        params.model_dump(),  # pure RegenParams — no _section_ids leak into the worker's RegenParams(**)
        section_ids,
    )

    return BookUploadResponse(book_id=book.id, job_id=job.id, regen_id=regen.id, status="regenerating")


@router.get("/api/books/{book_id}/regenerations", response_model=list[RegenerationOut])
async def list_regenerations(
    book_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> list[RegenerationOut]:
    """List all regenerations for a book, newest first."""
    result = await session.execute(
        select(Regeneration)
        .where(Regeneration.book_id == book_id)
        .order_by(desc(Regeneration.created_at))
    )
    return [RegenerationOut.model_validate(r) for r in result.scalars().all()]


@router.get("/api/regenerations/{regen_id}", response_model=RegenerationOut)
async def get_regeneration(
    regen_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> RegenerationOut:
    regen = await session.get(Regeneration, regen_id)
    if regen is None:
        raise HTTPException(404, detail="Regeneration not found")
    return RegenerationOut.model_validate(regen)


@router.post("/api/regenerations/{regen_id}/sections/{section_id}/rerun")
async def rerun_section(
    regen_id: UUID,
    section_id: str,
    body: dict[str, Any] = Body(default={}),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Re-run regeneration for a single section with optional custom instructions.

    Recap-aware: if the regen's recap_rule_ids opted into points_to_remember
    or any rename rule, this endpoint MUST mirror what the worker pre-loop
    would have done for this section:

      - PTR/Summary/Key-Takeaways source section  → write [] sentinel
        (no Gemini call), bullets stay redistributed in their target topics
      - Konnect/Note/Info-Edge/Info-Bytes source  → write [] sentinel
      - Regular topic that received Jaccard-assigned PTR bullets →
        recompute assignment + pass assigned_keypoints to regenerate_section
        so the LLM keeps the "Key Takeaways" subsection in this section's
        output (otherwise a per-section rerun silently drops it)
    """
    regen = await session.get(Regeneration, regen_id)
    if regen is None:
        raise HTTPException(404, detail="Regeneration not found")

    # Find the section by section_id string (e.g. "1.1")
    result = await session.execute(
        select(Section).where(
            Section.book_id == regen.book_id,
            Section.section_id == section_id,
        )
    )
    sec = result.scalar_one_or_none()
    if sec is None:
        raise HTTPException(404, detail=f"Section {section_id!r} not found")

    # Build params: inherit from original regen, override custom_instructions
    base_params = dict(regen.params or {})
    custom = body.get("custom_instructions", "")
    if custom:
        base_params["custom_instructions"] = custom
    # Legacy-value normalization: regenerations created before the tone
    # rename + language narrowing store old enum values (e.g. tone="academic",
    # language="ta"). Re-running them would 422 against the current RegenParams
    # schema, so map legacy values to their closest current equivalents.
    base_params = normalize_legacy_params(base_params)
    # recap_rule_ids might leak through base_params; that's fine — keep them
    params = RegenParams(**base_params)
    recap_ids = list(params.recap_rule_ids or [])

    # ─── Recap-aware pre-checks ──────────────────────────────────────
    assigned_keypoints: list[str] = []
    if recap_ids:
        from app.services.recap_config import (
            active_redistribute_rules,
            active_rename_rules,
            assign_bullets_to_sections,
            detect_redistribute_source_sections,
        )

        # Load ALL sections for the book to mirror worker pre-loop scope.
        all_secs_result = await session.execute(
            select(Section).where(Section.book_id == regen.book_id).order_by(Section.section_id)
        )
        all_secs = list(all_secs_result.scalars().all())
        section_tuples = [
            (s.section_id, s.title or "", list(s.blocks or []))
            for s in all_secs
        ]

        # 1. Is THIS section a PTR/Summary/Key-Takeaways source?
        if active_redistribute_rules(recap_ids):
            src_ids, _bullets = detect_redistribute_source_sections(section_tuples, recap_ids)
            if sec.section_id in src_ids:
                updated = dict(regen.blocks_by_section or {})
                updated[sec.section_id] = []
                regen.blocks_by_section = updated
                regen_qc = dict(regen.qc_drift or {})
                regen_qc[sec.section_id] = {
                    "pass": True,
                    "drifted": [],
                    "note": "section bullets redistributed via recap (rerun)",
                }
                regen.qc_drift = regen_qc
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(regen, "blocks_by_section")
                flag_modified(regen, "qc_drift")
                await session.flush()
                return {
                    "section_id": section_id,
                    "blocks": [],
                    "suppressed": True,
                    "reason": "ptr_source_redistributed",
                }

        # 2. Is THIS section a rename source (Konnect/Note/Info-Edge/Info-Bytes)?
        label_to_target: dict[str, str] = {}
        for r in active_rename_rules(recap_ids):
            for src in r["source_labels"]:
                label_to_target[src.lower()] = r["label"]
        if label_to_target.get((sec.title or "").strip().lower()):
            updated = dict(regen.blocks_by_section or {})
            updated[sec.section_id] = []
            regen.blocks_by_section = updated
            regen_qc = dict(regen.qc_drift or {})
            regen_qc[sec.section_id] = {
                "pass": True,
                "drifted": [],
                "note": "section promoted into preceding topic (rerun)",
            }
            regen.qc_drift = regen_qc
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(regen, "blocks_by_section")
            flag_modified(regen, "qc_drift")
            await session.flush()
            return {
                "section_id": section_id,
                "blocks": [],
                "suppressed": True,
                "reason": "rename_source_promoted",
            }

        # 3. Regular topic — recompute Jaccard assignment so the LLM gets
        #    the "Key Takeaways" directive for any bullets that belong
        #    to THIS topic.
        if active_redistribute_rules(recap_ids):
            src_ids, bullets = detect_redistribute_source_sections(section_tuples, recap_ids)
            if bullets:
                target_pool = [
                    (sid, title, blocks)
                    for sid, title, blocks in section_tuples
                    if sid not in src_ids
                ]
                per_section, _orphans = assign_bullets_to_sections(bullets, target_pool)
                assigned_keypoints = per_section.get(sec.section_id, [])

    # ─── Normal regen with optional assigned_keypoints ───────────────
    from app.services.regenerator import regenerate_section
    new_blocks = await regenerate_section(
        section_id=sec.section_id,
        section_title=sec.title,
        blocks=list(sec.blocks or []),
        params=params,
        assigned_keypoints=assigned_keypoints or None,
    )

    # Patch blocks_by_section in-place
    updated = dict(regen.blocks_by_section or {})
    updated[sec.section_id] = new_blocks
    regen.blocks_by_section = updated
    await session.flush()

    return {"section_id": section_id, "blocks": new_blocks}


@router.post("/api/regenerations/{regen_id}/save", response_model=dict)
async def save_regeneration(
    regen_id: UUID,
    body: dict[str, Any] = Body(...),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Save only confirmed sections to the regeneration record.

    Removes skipped sections from blocks_by_section so the Regenerated
    folder in the sidebar only shows confirmed content. Original sections
    are never modified.
    """
    regen = await session.get(Regeneration, regen_id)
    if regen is None:
        raise HTTPException(404, detail="Regeneration not found")

    confirmed_ids: list[str] = body.get("confirmed_section_ids", [])
    if not confirmed_ids:
        raise HTTPException(400, detail="No confirmed section IDs provided")

    current = dict(regen.blocks_by_section or {})
    # Keep only confirmed sections
    saved = {sid: blocks for sid, blocks in current.items() if sid in confirmed_ids}
    regen.blocks_by_section = saved
    await session.flush()

    return {"saved": True, "sections_saved": len(saved)}
