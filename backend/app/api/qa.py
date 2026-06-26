"""QA agent router.

Endpoints:
  POST /api/question-banks/{bank_id}/qa-run     — trigger deterministic QA pass
  GET  /api/question-banks/{bank_id}/qa-report  — latest per-section snapshot
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models.job import Job
from app.models.qa_run import QARun
from app.models.question import Question
from app.models.question_bank import QuestionBank

router = APIRouter(prefix="/api/question-banks", tags=["qa"])


@router.post("/{bank_id}/qa-run", status_code=status.HTTP_202_ACCEPTED)
async def trigger_qa_run(
    bank_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Kick off a deterministic QA pass over every question in the bank.

    Fidelity only (Pillar B). Returns immediately with the Job id — progress is
    surfaced through the existing /api/jobs endpoint.
    """
    bank = await session.get(QuestionBank, bank_id)
    if bank is None:
        raise HTTPException(404, detail="QuestionBank not found")

    count_row = await session.execute(
        select(func.count(Question.id)).where(Question.bank_id == bank_id)
    )
    if (count_row.scalar() or 0) == 0:
        raise HTTPException(400, detail="Bank has no questions to score")

    job = Job(book_id=bank.book_id, type="run_qa_fidelity", status="queued", progress=0)
    session.add(job)
    await session.flush()
    await session.commit()

    import app.workers.qa  # noqa: F401 — ensure task registration
    from app.workers.runner import dispatch

    dispatch("run_qa_fidelity", str(bank.id), str(job.id))

    return {
        "bank_id": str(bank.id),
        "job_id": str(job.id),
        "status": "queued",
    }


@router.get("/{bank_id}/qa-report")
async def get_qa_report(
    bank_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Return the most recent QA snapshot per section plus rolled-up totals.

    Shape:
      {
        bank_id, bank_score, tally: {passed, flagged, failed},
        sections: [{section_ref, score, verbatim_count, ..., failures: [...]}]
      }
    """
    bank = await session.get(QuestionBank, bank_id)
    if bank is None:
        raise HTTPException(404, detail="QuestionBank not found")

    # Per-section snapshots — one row per section (latest, since worker wipes
    # prior runs).
    run_rows = await session.execute(
        select(QARun)
        .where(QARun.bank_id == bank_id)
        .order_by(QARun.section_ref, QARun.created_at.desc())
    )
    runs = run_rows.scalars().all()

    # Tally by qc_status across all Question rows — source of truth for the
    # bank-level pass/flag/fail counts (QARun.failures lists non-passed only).
    tally_rows = await session.execute(
        select(Question.qc_status, func.count(Question.id))
        .where(Question.bank_id == bank_id)
        .group_by(Question.qc_status)
    )
    tally = {status: int(n) for status, n in tally_rows.all()}

    score_row = await session.execute(
        select(func.avg(Question.qc_score))
        .where(Question.bank_id == bank_id)
        .where(Question.qc_score.is_not(None))
    )
    bank_score = score_row.scalar()

    sections_out = []
    total_missed = 0
    total_hallucinated = 0
    for r in runs:
        total_missed += int(r.missed or 0)
        total_hallucinated += int(r.hallucinated or 0)
        sections_out.append({
            "section_ref": r.section_ref,
            "score": round(float(r.score or 0.0), 3),
            "expected_total": r.expected_total,
            "extracted_total": r.extracted_total,
            "missed": r.missed,
            "hallucinated": r.hallucinated,
            "verbatim_count": r.verbatim_count,
            "paraphrased_count": r.paraphrased_count,
            "not_verbatim_count": r.not_verbatim_count,
            "failures": r.failures or [],
            "model": r.model,
            "prompt_version": r.prompt_version,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })

    return {
        "bank_id": str(bank.id),
        "bank_score": round(float(bank_score), 3) if bank_score is not None else None,
        "tally": {
            "passed": tally.get("passed", 0),
            "flagged": tally.get("flagged", 0),
            "failed": tally.get("failed", 0),
            "pending": tally.get("pending", 0),
        },
        "missed": total_missed,
        "hallucinated": total_hallucinated,
        "sections": sections_out,
    }
