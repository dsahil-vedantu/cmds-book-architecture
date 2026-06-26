"""Real cancellation — kill running tasks AND stop the driver re-driving.

Before this, ``cancel-all`` only flipped Job.status to ``cancelled`` in
Postgres. That did nothing to a task already executing in the Celery worker
(it kept running until it finished or hit the 30-min hard limit), and the
state-driven driver would re-dispatch the book on its next tick because the
Book row was still in an in-flight status. Net effect: cancellation required a
backend restart to take hold. Not viable for production.

This module fixes both halves:

  1. ``revoke_task(job.id)`` actually SIGTERMs the worker child running the
     task (the Celery task id == the Job id — see runner.dispatch). The slot
     frees immediately.
  2. The Book is moved to a terminal ``cancelled`` status and any ``running``
     stage is set to ``cancelled``. The driver (``_drive_active_books`` selects
     only in-flight book statuses) skips it, and the coordinator (which only
     CAS-re-dispatches a stage from ``pending``/``failed``) can't restart it.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.book import Book
from app.models.job import Job
from app.workers.runner import purge_and_kill_all, revoke_task

# Job statuses considered "in flight" (not yet terminal).
_INFLIGHT_JOB = ("queued", "running", "started", "pending")
# Book statuses the state-driven driver actively re-drives. Mirrors
# watchdog._INFLIGHT_STATUSES — keep in sync.
_INFLIGHT_BOOK = ("analysing", "extracting", "processing", "schema_ready")
_STAGE_ATTRS = ("schema_status", "theory_status", "questions_status", "figures_status")


async def cancel_books(
    session: AsyncSession,
    *,
    book_ids: list[UUID] | None = None,
    reason: str = "Cancelled by user",
) -> dict:
    """Cancel in-flight work and make it STAY cancelled (no restart needed).

    ``book_ids=None`` → cancel everything in flight (the cancel-all case).
    Otherwise → cancel only the named books.

    Returns counts for the API response.
    """
    # ── 1. In-flight jobs → revoke the live Celery task + mark cancelled ──
    job_q = select(Job).where(Job.status.in_(_INFLIGHT_JOB))
    if book_ids is not None:
        job_q = job_q.where(Job.book_id.in_(book_ids))
    jobs = list((await session.execute(job_q)).scalars().all())

    revoked = 0
    now = datetime.utcnow()
    for job in jobs:
        # task_id == job.id (set at dispatch) → this kills the exact task.
        if revoke_task(job.id):
            revoked += 1
        job.status = "cancelled"
        job.error = reason
        job.finished_at = now

    # ── 2. Books → terminal status so the driver stops re-driving them ──
    if book_ids is not None:
        book_q = select(Book).where(Book.id.in_(book_ids))
    else:
        book_q = select(Book).where(Book.status.in_(_INFLIGHT_BOOK))
    books = list((await session.execute(book_q)).scalars().all())

    cancelled_books = 0
    for book in books:
        touched = False
        # Only terminalize a book that's actually in flight — never clobber a
        # book that already finished (ready/failed) or hasn't started.
        if book.status in _INFLIGHT_BOOK:
            book.status = "cancelled"
            touched = True
        # Any running stage → cancelled. "cancelled" is outside the
        # coordinator's CAS from-states (pending/failed), so it won't be
        # re-dispatched, and it's not "running", so dead-worker detection
        # ignores it too.
        for attr in _STAGE_ATTRS:
            if getattr(book, attr, None) == "running":
                setattr(book, attr, "cancelled")
                touched = True
        if touched:
            cancelled_books += 1

    await session.commit()

    # Cancel-all (book_ids is None) → also nuke the broker: purge the queued
    # backlog and SIGTERM every running task. This is what actually frees a
    # worker that's saturated by redelivered zombie tasks (the "every upload
    # just queues" trap). For a targeted single-book cancel we rely on the
    # per-job revoke above instead, so we don't disturb other books' work.
    purge_result = {"purged": 0, "killed": 0}
    if book_ids is None:
        purge_result = purge_and_kill_all()

    return {
        "jobs_cancelled": len(jobs),
        "tasks_revoked": revoked,
        "books_cancelled": cancelled_books,
        "queue_purged": purge_result["purged"],
        "running_killed": purge_result["killed"],
    }
