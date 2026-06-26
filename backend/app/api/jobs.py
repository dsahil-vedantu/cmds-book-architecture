"""Jobs router — poll job status or subscribe via SSE."""

from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import async_session_factory, get_session
from app.models.job import Job
from app.schemas.job import JobOut
from app.services.cancellation import cancel_books
from app.utils.sse import sse_event

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


@router.post("/cancel-all")
async def cancel_all_jobs(
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Cancel every in-flight job FOR REAL — no backend restart required.

    Kills the running Celery tasks (``revoke(terminate=True)`` via the Job-id
    == task-id link) and moves their books to a terminal ``cancelled`` status
    so the state-driven driver stops re-driving them. See
    ``app.services.cancellation`` for the full rationale.
    """
    result = await cancel_books(session, book_ids=None,
                                reason="Cancelled via /api/jobs/cancel-all")
    return {
        "cancelled": result["jobs_cancelled"],
        **result,
        "message": "Tasks revoked and books terminated — no restart needed.",
    }


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: UUID, session: AsyncSession = Depends(get_session)) -> JobOut:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, detail="Job not found")
    return JobOut.model_validate(job)


@router.get("/{job_id}/stream")
async def stream_job(job_id: UUID) -> StreamingResponse:
    """SSE endpoint — polls the DB row every second and emits updates until terminal."""

    async def event_generator():
        last_snapshot: dict | None = None
        ticks = 0
        while True:
            async with async_session_factory() as session:
                job = await session.get(Job, job_id)
            if job is None:
                yield sse_event({"error": "job_not_found"}, event="error")
                return

            snapshot = {
                "status": job.status,
                "progress": job.progress,
                "message": job.message,
                "error": job.error,
            }
            if snapshot != last_snapshot:
                yield sse_event(snapshot, event="update")
                last_snapshot = snapshot

            if job.status in _TERMINAL_STATUSES:
                yield sse_event({"final": True, **snapshot}, event="done")
                return

            ticks += 1
            if ticks > 60 * 30:  # 30-minute cap
                yield sse_event({"error": "timeout"}, event="error")
                return
            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
