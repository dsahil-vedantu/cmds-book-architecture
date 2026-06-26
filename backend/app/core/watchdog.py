"""Background task that fails jobs whose worker has stopped responding.

The previous reliability story was: if a worker hangs, the recovery handler
on the NEXT startup will re-dispatch jobs >35 min old. That's too long —
the UI shows a frozen progress bar for half an hour and the user has no
signal that anything is wrong.

The watchdog runs inside the API process, polls every ``CHECK_INTERVAL_S``,
and marks any ``running`` job whose ``last_heartbeat_at`` is older than
``STALE_AFTER_S`` as ``failed``. The error message tells the user clearly
that the job was killed by the watchdog so they can retry.

This is the first line of defence. The startup recovery handler still runs
for the case where the API itself crashed (no in-flight watchdog).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models.job import Job

logger = logging.getLogger(__name__)

# How often the watchdog ticks. This is now also the cadence of the
# state-driven driver (_drive_active_books), so it doubles as the maximum
# delay before a book with a lost dispatch message gets re-driven. 20s is
# responsive enough that a dropped hand-off is invisible to users, while
# the per-tick work (a handful of indexed queries + idempotent coordinator
# calls over only the active books) stays negligible.
CHECK_INTERVAL_S = 20
# A running job is stale if its heartbeat (or started_at, when heartbeat is
# NULL — e.g. a job started before this column existed) is older than this.
#
# Threshold raised 300 → 900s (15 min): the previous 300s was killing
# legitimate work under concurrent upload pressure. Observed: 2 PDFs
# uploaded simultaneously → both books fired schema-gen Gemini calls
# → each call queued behind the 8-slot in-flight semaphore + slower
# individual Gemini response under contention → schema call took 341s
# → watchdog killed at 341s (just past 300s threshold) → schema marked
# failed, user had to click Retry. The Gemini per-call hard timeout is
# 150s, retries 2x with backoff = 1+2+4 = ~157s + 3×150 = ~607s
# worst-case for a single section attempt under retries. 15 min covers
# this plus headroom for queue waits during multi-book contention.
# True hangs (worker crashed mid-call) still get caught — 15 min of
# zero heartbeat is unambiguous.
STALE_AFTER_S = 900

# A QUEUED job is "zombie" if it's been sitting in `queued` (never started)
# for longer than this. In prod (Celery+Redis) this happens when:
#   • a worker died after acking the Celery message but before starting work
#   • the dispatch landed in Redis but no worker is consuming (broker hiccup,
#     container restart between dispatch and pickup, queue saturation)
# The original watchdog only killed RUNNING jobs with stale heartbeats — a
# job stuck in `queued` has neither started_at nor heartbeat, so it slipped
# through and shielded the book from reconciliation (reconciler skips books
# that have ANY queued/running job). 5 minutes is comfortably longer than a
# legitimate queue wait, and short enough that the next reconciler scan
# (~60s later) actually re-drives the book.
QUEUE_STALE_AFTER_S = 300

# ORCH Day 11 — how long to wait before force-releasing an
# extraction_lock_at. Matches MAX orchestrator lock timeout
# (workers/orchestrator.py:LOCK_TIMEOUT_MIN). 5 min after watchdog
# kills stale Jobs, so Job cleanup happens first, then locks release.
ORCH_LOCK_TIMEOUT_MIN = 10

# Build Step 1 — Reconciliation safety net.
# Event-only orchestration breaks permanently if any dispatch event is
# lost (crash, OOM, exception between status-write and dispatch). The
# reconciler periodically re-drives in-flight books that have gone stale
# with no live Job, so "stuck forever" is architecturally impossible.
#
# RECONCILE_BATCH    — max stalled books re-driven per scan (anti-herd).
# MAX_RECOVERY_ATTEMPTS — give up after this many re-drives and mark the
#                      book failed (anti-infinite-loop / burning Gemini).
#                      Reset to 0 in the coordinator on real forward
#                      progress, so a recovered book gets the full budget
#                      again on any later legitimate stall.
RECONCILE_BATCH = 5
MAX_RECOVERY_ATTEMPTS = 5

# In-flight book.status values the reconciler is allowed to touch.
#
# `schema_ready` was originally excluded because it could mean "user needs
# to approve" (needs_review path). But in practice, the orchestrator's
# coordinator AUTO-ADVANCES books with schema_status in ('done','needs_review')
# straight to theory dispatch — there is no user-approval gate in code.
#
# The architectural gap: analyser worker fires the coordinator via Celery
# dispatch, but that one message can be lost (broker hiccup, container
# restart in the ack window, queue purge). When lost, the book sits at
# schema_ready forever because the reconciler never sees it.
#
# Including schema_ready here closes the gap: the reconciler will re-fire
# the coordinator within ~15 min, which then auto-advances the book.
# Terminal states (ready/failed/partial) remain excluded.
_INFLIGHT_STATUSES = ("analysing", "extracting", "processing", "schema_ready")


# Fast dead-worker detection (fix #3 completion). Every stage worker runs a
# background heartbeat thread that beats every 10s (theory/questions/figures)
# or 30s (schema), independent of what Gemini is doing. So a stage that is
# `running` whose job heartbeat has gone stale beyond this threshold means the
# WORKER PROCESS IS DEAD (crash / OOM / SIGKILL), not merely busy. The driver
# fails such a stage immediately so the coordinator re-drives it on the same
# tick — crash recovery in ~2 min instead of waiting for the 15-min stale-job
# killer. 120s = comfortably above the 30s max beat interval × slack, so a
# live-but-slow worker is never false-killed.
WORKER_DEAD_AFTER_S = 120

# Map each book stage-status column to the Job.type that backs it (set by
# orchestrator._new_job). Used to find the heartbeat for a running stage.
_STAGE_JOB_TYPE = {
    "schema_status": "analyse",
    "theory_status": "extract",
    "questions_status": "extract_questions",
    "figures_status": "extract_figures",
}

# Per-stage retry counter used to BOUND crash re-drives (so a poison input
# that crashes the worker every time can't loop forever burning Gemini).
_STAGE_RETRY_ATTR = {
    "schema": "schema_retries",
    "theory": "theory_retries",
    "questions": "questions_retries",
    "figures": "figures_retries",
}

# How many times we'll re-drive a stage after a worker crash before giving up
# and marking it failed. Generous (crashes are infrastructure, not bad input)
# but bounded. Distinct from MAX_AUTO_RETRIES (content-failure retries): a
# crash means the stage NEVER COMPLETED, so we re-run it fresh (→ pending)
# rather than consuming the content-retry budget (→ failed).
MAX_CRASH_REDRIVES = 5


_engine = create_engine(
    settings.SYNC_DATABASE_URL,
    pool_pre_ping=True,
    pool_size=2, max_overflow=3, pool_timeout=10, pool_recycle=900,
)
_WatchdogSession = sessionmaker(bind=_engine, class_=Session, autoflush=False)


def _fail_dead_worker_stages(session, book_id) -> int:
    """Fail any stage whose backing worker is dead (stale heartbeat).

    A stage is considered dead-worker ONLY when its job has beaten at least
    once (``last_heartbeat_at`` is not NULL) and then gone stale beyond
    WORKER_DEAD_AFTER_S. The "beaten at least once" guard is critical: it
    avoids false-killing a job that hasn't started its heartbeat yet (e.g.
    a figures job blocked on its in-flight semaphore, or any job in the brief
    window between dispatch and entering its Heartbeat context). Those rare
    pre-first-beat deaths are left to the slower 900s stale-job killer.

    On detection: CAS the stage running→failed and fail the orphan Job row.
    The coordinator (called right after, by the driver) then sees `failed`
    and retries it (subject to MAX_AUTO_RETRIES).

    Returns the number of stages failed.
    """
    from app.models.book import Book
    from app.workers.orchestrator import cas_set_stage

    book = session.get(Book, book_id)
    if book is None:
        return 0

    dead_cutoff = datetime.now(timezone.utc) - timedelta(seconds=WORKER_DEAD_AFTER_S)
    failed = 0
    for stage_attr, job_type in _STAGE_JOB_TYPE.items():
        if getattr(book, stage_attr) != "running":
            continue
        job = session.execute(
            select(Job)
            .where(Job.book_id == book_id, Job.type == job_type)
            .order_by(Job.created_at.desc())
        ).scalars().first()
        # Only act on a job that beat at least once then went stale. NULL
        # heartbeat → hasn't started beating yet → not our case (900s killer).
        if job is None or job.last_heartbeat_at is None:
            continue
        hb = job.last_heartbeat_at
        if hb.tzinfo is None:
            hb = hb.replace(tzinfo=timezone.utc)
        if hb >= dead_cutoff:
            continue  # heartbeat fresh → worker alive
        # Worker is dead. The stage NEVER COMPLETED (the worker died mid-run),
        # so the correct recovery is to RE-RUN IT FRESH → revert to `pending`,
        # NOT `failed`. `pending` routes the coordinator to dispatch_* (a clean
        # re-dispatch) instead of retry_* — so a crash does not consume the
        # content-failure retry budget (MAX_AUTO_RETRIES) and can't strand the
        # book. Crash-loops are bounded separately by MAX_CRASH_REDRIVES.
        stage = stage_attr.replace("_status", "")
        retry_attr = _STAGE_RETRY_ATTR[stage]
        attempts = getattr(book, retry_attr, 0) or 0
        if attempts >= MAX_CRASH_REDRIVES:
            target, give_up = "failed", True
        else:
            setattr(book, retry_attr, attempts + 1)
            session.commit()  # persist the bump before the CAS
            target, give_up = "pending", False
        if cas_set_stage(session, book_id, stage, target, from_states=("running",)):
            if job.status == "running":
                job.status = "failed"
                job.finished_at = datetime.now(timezone.utc)
                job.error = (
                    f"Watchdog: worker dead — no heartbeat for "
                    f">{WORKER_DEAD_AFTER_S}s."
                )
                session.commit()
            failed += 1
            age = int((datetime.now(timezone.utc) - hb).total_seconds())
            if give_up:
                logger.error(
                    "driver: dead-worker stage book=%s stage=%s exceeded "
                    "%d crash re-drives — marking FAILED (likely poison input)",
                    book_id, stage, MAX_CRASH_REDRIVES,
                )
            else:
                logger.warning(
                    "driver: dead-worker stage book=%s stage=%s "
                    "(heartbeat %ss stale) → pending, re-run %d/%d",
                    book_id, stage, age, attempts + 1, MAX_CRASH_REDRIVES,
                )
    return failed


def _scan_once() -> int:
    """Mark stale running jobs as failed. Returns count killed."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=STALE_AFTER_S)
    queue_cutoff = datetime.now(timezone.utc) - timedelta(seconds=QUEUE_STALE_AFTER_S)
    killed = 0
    with _WatchdogSession() as session:
        # ── Fix A — zombie QUEUED jobs ──────────────────────────────────
        # A job stuck in `queued` past QUEUE_STALE_AFTER_S means no Celery
        # worker ever picked it up (broker hiccup, container restart in the
        # ack window, or queue saturation). Without killing these, the
        # reconciler's "has_live_job" check stays True and the book never
        # gets re-driven. Failing them here frees the reconciler to act on
        # its next scan (within ~60s).
        stale_queued = session.execute(
            select(Job).where(
                Job.status == "queued",
                Job.created_at < queue_cutoff,
                Job.finished_at.is_(None),
            )
        ).scalars().all()
        for job in stale_queued:
            created_at = job.created_at
            if created_at and created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            age_s = int((datetime.now(timezone.utc) - created_at).total_seconds()) if created_at else -1
            job.status = "failed"
            job.finished_at = datetime.now(timezone.utc)
            job.error = (
                f"Watchdog: queued for {age_s}s with no worker pick-up "
                f"(threshold {QUEUE_STALE_AFTER_S}s). Likely Celery worker "
                "died or broker hiccup. Reconciler will re-drive the book."
            )
            killed += 1
            logger.warning(
                "watchdog killed zombie queued job %s type=%s age=%ss book=%s",
                job.id, job.type, age_s, job.book_id,
            )

        rows = session.execute(
            select(Job).where(
                Job.status == "running",
                Job.finished_at.is_(None),
            )
        ).scalars().all()
        for job in rows:
            # Use heartbeat if present; otherwise fall back to started_at.
            # If both are NULL the job is freshly queued — leave it alone.
            ref = job.last_heartbeat_at or job.started_at
            if ref is None:
                continue
            # SQLite stores naive datetimes; force UTC-aware for comparison.
            if ref.tzinfo is None:
                ref = ref.replace(tzinfo=timezone.utc)
            if ref >= cutoff:
                continue
            age_s = int((datetime.now(timezone.utc) - ref).total_seconds())
            job.status = "failed"
            job.finished_at = datetime.now(timezone.utc)
            job.error = (
                f"Watchdog: no progress for {age_s}s "
                f"(threshold {STALE_AFTER_S}s). The worker hung or crashed. "
                "Click retry to re-run."
            )
            killed += 1
            logger.warning(
                "watchdog killed stale job %s type=%s age=%ss",
                job.id,
                job.type,
                age_s,
            )
        if killed:
            session.commit()

        # Phase 7 (CONTRACT.md §5): also catch orphan per-stage status.
        # If a Book has stage_status="running" but no live Job exists for
        # that book, the worker died mid-stage (OOM, container restart,
        # process crash). Mark the stage failed so the user can retry,
        # and derive book.status accordingly.
        from app.models.book import Book
        from app.services.book_status import derive_book_status

        book_rows = session.execute(
            select(Book).where(
                (Book.schema_status == "running")
                | (Book.theory_status == "running")
                | (Book.questions_status == "running")
                | (Book.figures_status == "running")
            )
        ).scalars().all()
        for book in book_rows:
            # Does the book still have a live Job?
            has_live_job = session.execute(
                select(Job).where(
                    Job.book_id == book.id,
                    Job.status.in_(["queued", "running"]),
                ).limit(1)
            ).scalars().first()
            if has_live_job is not None:
                continue  # legitimate in-flight
            # No live Job, but stage(s) say "running" → orphan. Fail them.
            any_orphan_marked = False
            for stage in (
                "schema_status", "theory_status",
                "questions_status", "figures_status",
            ):
                if getattr(book, stage) == "running":
                    setattr(book, stage, "failed")
                    logger.warning(
                        "watchdog: orphan running stage book=%s %s "
                        "(no live Job)",
                        book.id, stage,
                    )
                    killed += 1
                    any_orphan_marked = True
            book.status = derive_book_status(book)
            session.commit()

            # Day 15 fix — fire coordinator after orphan cleanup so the
            # state machine can auto-retry (if retry budget remains) or
            # finalize. Without this dispatch, the book sits in 'failed'
            # forever and the auto-retry safety net never activates.
            # Same pattern as the stale-lock branch below.
            if any_orphan_marked:
                try:
                    from app.workers.runner import dispatch
                    dispatch("coordinate_extraction", str(book.id))
                except Exception as e:
                    logger.warning(
                        "watchdog: orphan-cleanup coordinator dispatch "
                        "failed for book=%s: %s",
                        book.id, e,
                    )

        # ORCH Day 11 — force-release stale orchestrator locks.
        # The coordinator normally holds extraction_lock_at only during
        # its own execution and releases it in a finally block. If the
        # coordinator process is killed before reaching finally (OOM,
        # container restart, SIGKILL), the lock stays set and the book
        # is stuck — the next dispatch sees the lock held and exits.
        # Force-release locks older than this threshold and re-dispatch
        # the coordinator so the state machine can advance.
        lock_cutoff = datetime.now(timezone.utc) - timedelta(
            minutes=ORCH_LOCK_TIMEOUT_MIN,
        )
        stale_locked = session.execute(
            select(Book).where(
                Book.extraction_lock_at.is_not(None),
                Book.extraction_lock_at < lock_cutoff,
            )
        ).scalars().all()
        for book in stale_locked:
            ref = book.extraction_lock_at
            if ref is not None and ref.tzinfo is None:
                ref = ref.replace(tzinfo=timezone.utc)
            age_s = int((datetime.now(timezone.utc) - ref).total_seconds()) if ref else -1
            logger.warning(
                "watchdog: force-releasing stale orchestrator lock "
                "(book=%s, age=%ss, threshold=%dmin) + re-dispatching "
                "coordinator",
                book.id, age_s, ORCH_LOCK_TIMEOUT_MIN,
            )
            book.extraction_lock_at = None
            session.commit()
            # Re-fire the coordinator so the state machine moves forward.
            # Idempotent — the coordinator will re-acquire the lock,
            # check current state, and either dispatch or no-op.
            try:
                from app.workers.runner import dispatch
                dispatch("coordinate_extraction", str(book.id))
            except Exception as e:
                logger.warning(
                    "watchdog: coordinator re-dispatch failed for book=%s: %s",
                    book.id, e,
                )
            killed += 1

        # Build Step 1 — reconciliation safety net. Runs AFTER the
        # stale-job, orphan-stage, and stale-lock branches so any locks
        # those branches released are visible here. Re-drives in-flight
        # books that have gone stale with no live Job by dispatching the
        # idempotent coordinator (NOT workers directly — the coordinator's
        # CAS-lock handles concurrency).
        killed += _reconcile_stalled_books(session)

    return killed


def _reconcile_stalled_books(session) -> int:
    """Re-drive in-flight books that stalled with no live Job.

    The single safety net that makes "stuck forever" impossible. A book
    is stalled when:
      - book.status is in-flight (analysing/extracting/processing) — never
        ready/failed/partial/schema_ready, so terminal + awaiting-approval
        + needs_review books are untouched.
      - it hasn't been updated for STALE_AFTER_S (don't race fresh
        dispatches — updated_at auto-bumps on every status write).
      - no live Job (queued/running) exists for it.

    Guards (Part 1d):
      - Concurrency: we dispatch the coordinator, which CAS-locks. Two
        watchdogs → only one coordinator proceeds.
      - Staleness: updated_at < now - STALE_AFTER_S.
      - Herd: LIMIT RECONCILE_BATCH per scan.
      - Loop: recovery_attempts cap → mark failed and stop.
      - Terminal-safe: status filter excludes ready/failed/partial; an
        extra schema_status != 'needs_review' guard belts-and-suspenders
        the needs_review case (book.status is 'schema_ready' there, so it
        wouldn't match the in-flight filter anyway).

    Returns the number of books re-driven OR failed this scan.
    """
    from app.models.book import Book
    from app.services.book_status import derive_book_status

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=STALE_AFTER_S)

    candidates = session.execute(
        select(Book)
        .where(
            Book.status.in_(_INFLIGHT_STATUSES),
            Book.schema_status != "needs_review",
            Book.updated_at < cutoff,
        )
        .order_by(Book.updated_at.asc())
    ).scalars().all()

    acted = 0
    for book in candidates:
        if acted >= RECONCILE_BATCH:
            break  # anti-thundering-herd — leave the rest for next scan

        # ── Fix B — heartbeat-aware liveness check ──────────────────────
        # A Job row in queued/running is treated as "live work" ONLY if it
        # is FRESH (running with a recent heartbeat, OR queued within the
        # last QUEUE_STALE_AFTER_S). Without this, a zombie queued Job
        # (worker never picked it up) made the book look in-flight and the
        # reconciler skipped it forever. Fix A above will eventually kill
        # the zombie, but this guard makes the reconciler immune even if
        # _scan_once hasn't run yet.
        from sqlalchemy import or_, and_
        heartbeat_cutoff = datetime.now(timezone.utc) - timedelta(seconds=180)
        queued_freshness_cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=QUEUE_STALE_AFTER_S)
        )
        has_live_job = session.execute(
            select(Job).where(
                Job.book_id == book.id,
                Job.status.in_(["queued", "running"]),
                or_(
                    # Running job with a recent heartbeat → genuinely alive.
                    and_(
                        Job.status == "running",
                        Job.last_heartbeat_at.is_not(None),
                        Job.last_heartbeat_at > heartbeat_cutoff,
                    ),
                    # Running job without heartbeat but started recently
                    # (the heartbeat hasn't ticked yet on the first cycle).
                    and_(
                        Job.status == "running",
                        Job.last_heartbeat_at.is_(None),
                        Job.started_at.is_not(None),
                        Job.started_at > heartbeat_cutoff,
                    ),
                    # Queued job that's still fresh (likely will be picked
                    # up imminently). Anything older is a zombie.
                    and_(
                        Job.status == "queued",
                        Job.created_at > queued_freshness_cutoff,
                    ),
                ),
            ).limit(1)
        ).scalars().first()
        if has_live_job is not None:
            continue

        # Anti-infinite-loop: cap recovery attempts.
        if (book.recovery_attempts or 0) >= MAX_RECOVERY_ATTEMPTS:
            # Mark the book failed + fail the pending/in-flight stage so
            # the user sees a clear terminal state instead of a silent
            # spinner. Stop re-driving (don't burn Gemini calls).
            for stage in (
                "schema_status", "theory_status",
                "questions_status", "figures_status",
            ):
                if getattr(book, stage) in ("pending", "running"):
                    setattr(book, stage, "failed")
                    break
            book.status = derive_book_status(book)
            session.commit()
            logger.error(
                "reconcile: book %s exceeded recovery cap (%d) → failed",
                book.id, MAX_RECOVERY_ATTEMPTS,
            )
            acted += 1
            continue

        # Re-drive via the unified coordinator. It decides the right next
        # step (dispatch_analyse / dispatch_theory / retry / finalize).
        book.recovery_attempts = (book.recovery_attempts or 0) + 1
        session.commit()
        try:
            from app.workers.runner import dispatch
            dispatch("coordinate_extraction", str(book.id))
            logger.warning(
                "reconcile: re-driving stalled book %s (attempt %d/%d)",
                book.id, book.recovery_attempts, MAX_RECOVERY_ATTEMPTS,
            )
        except Exception as e:
            logger.warning(
                "reconcile: coordinator dispatch failed for book=%s: %s",
                book.id, e,
            )
        acted += 1

    return acted


def _drive_active_books() -> int:
    """PRIMARY state-driven engine — step every active book forward.

    This is fix #3 (the Kubernetes-controller pattern): instead of relying
    on a worker-delivered ``coordinate_extraction`` message to advance each
    book (message-driven → a lost message strands the book forever), we
    continuously re-read DB state and drive every non-terminal book through
    the coordinator on a fast tick.

    Two deliberate design choices make this robust:

    1. We run the coordinator **inline, in this (API) process** — NOT via
       ``dispatch("coordinate_extraction")``. The coordinator does no heavy
       work (pure DB inspection + stage-worker dispatch), so it's safe and
       fast to run here. Crucially, this means forward progress continues
       **even if the Celery worker is dead** — the API process drives the
       state machine and the worker only does the actual extraction.

    2. We drive **every** active book unconditionally, with no staleness
       gate. The coordinator is idempotent, lock-guarded (CAS extraction
       lock), and returns ``no_action`` when work is genuinely in-flight,
       so calling it on a busy book every tick is cheap and side-effect
       free. A lost dispatch message becomes a ≤CHECK_INTERVAL_S delay
       instead of a permanent stall.

    The happy-path worker-tail dispatches still fire (instant hand-off);
    they're now a latency optimization, not a correctness requirement.

    Returns the number of books driven.
    """
    from app.models.book import Book

    # Materialise the id list in a short-lived session, then run the
    # coordinator (which opens its own session) per book — no nested-cursor
    # entanglement.
    with _WatchdogSession() as session:
        book_ids = session.execute(
            select(Book.id).where(Book.status.in_(_INFLIGHT_STATUSES))
        ).scalars().all()

    if not book_ids:
        return 0

    from app.workers.orchestrator import _coordinate_extraction

    driven = 0
    for book_id in book_ids:
        # 1. Liveness: fail any stage whose worker is dead (stale heartbeat),
        #    so the coordinator below retries it THIS tick (~2 min crash
        #    recovery) instead of waiting for the 15-min stale-job killer.
        try:
            with _WatchdogSession() as s:
                _fail_dead_worker_stages(s, book_id)
        except Exception as e:
            logger.warning(
                "driver: liveness check failed for book=%s: %s", book_id, e,
            )
        # 2. Drive the state machine forward (a just-failed stage → retry;
        #    a genuinely in-flight stage → no_action).
        try:
            _coordinate_extraction(str(book_id))
            driven += 1
        except Exception as e:
            logger.warning(
                "driver: coordinator failed for book=%s: %s", book_id, e,
            )
    logger.debug("driver: stepped %d active book(s)", driven)
    return driven


# Job / book statuses at which a still-running worker task is a ZOMBIE.
_TERMINAL_JOB_STATUSES = frozenset(
    {"cancelled", "succeeded", "failed", "done", "error", "completed"}
)
_TERMINAL_BOOK_STATUSES = frozenset({"cancelled", "failed", "ready", "partial"})


def _reap_zombie_tasks() -> int:
    """Automatically kill redelivered zombie tasks — no cancel-all needed.

    Celery's crash-safety settings (task_acks_late + task_reject_on_worker_lost)
    REDELIVER every task that was in-flight at a container restart. For a book
    that has since been cancelled / failed / completed, that redelivered task
    re-runs its full multi-minute extraction, saturates the worker, and blocks
    fresh uploads ("queued — waiting for worker"). This reaps exactly those.

    How it stays surgical (zero false positives):
      • Stage tasks are dispatched with Celery task_id == Job id (see
        runner.dispatch), so the active task's id IS its job id.
      • A LEGIT running task → its job is still 'running'/'queued' → skipped.
      • A ZOMBIE → its job is already terminal (the original run finished or was
        cancelled) AND its book is terminal → killed.
      • A regen / coordinator / verify task → its id is NOT a job id → the
        lookup misses → skipped. So live regen work is never touched.

    Runs every watchdog tick. No-op inline or when the worker doesn't answer.
    """
    if settings.TASK_EXECUTOR != "celery":
        return 0
    try:
        from app.workers.celery_app import celery_app

        active = celery_app.control.inspect(timeout=5).active() or {}
    except Exception as e:  # broker hiccup / worker busy — try again next tick
        logger.debug("reaper: inspect failed: %s", e)
        return 0

    task_ids = [
        t.get("id")
        for tasks in active.values()
        for t in (tasks or [])
        if t.get("id")
    ]
    if not task_ids:
        return 0

    from uuid import UUID

    from app.models.book import Book

    reaped = 0
    with _WatchdogSession() as session:
        for tid in task_ids:
            try:
                job_uuid = UUID(str(tid))
            except (ValueError, TypeError):
                continue  # not a job-keyed task (regen/coordinator/verify) → leave it
            job = session.get(Job, job_uuid)
            if job is None or job.status not in _TERMINAL_JOB_STATUSES:
                continue  # legit in-flight task → leave it running
            # Job terminal but task still executing. Confirm the book is also
            # terminal before killing, so we never cut the tail of a task that
            # just marked its job done while the book is still finalizing.
            book = session.get(Book, job.book_id) if job.book_id else None
            if book is not None and book.status not in _TERMINAL_BOOK_STATUSES:
                continue
            try:
                celery_app.control.revoke(str(tid), terminate=True, signal="SIGTERM")
                reaped += 1
                logger.info(
                    "reaper: killed zombie task=%s (job.status=%s book.status=%s)",
                    tid, job.status, book.status if book else "deleted",
                )
            except Exception as e:
                logger.warning("reaper: revoke failed task=%s: %s", tid, e)
    if reaped:
        logger.info("reaper: killed %d zombie task(s) this tick", reaped)
    return reaped


async def watchdog_loop() -> None:
    """Run forever, polling every CHECK_INTERVAL_S. Cancelled on shutdown.

    Each tick runs two phases:
      • _drive_active_books() — the PRIMARY state-driven engine (fix #3):
        steps every active book's state machine forward, in-process, so a
        lost dispatch message never strands a book.
      • _scan_once() — the cleanup/backstop layer: reaps dead jobs, fails
        orphan stages, releases stale locks, and (via the reconciler) fails
        books that are genuinely broken past the recovery cap.
    """
    logger.info(
        "watchdog started — interval=%ss stale_after=%ss (state-driven driver active)",
        CHECK_INTERVAL_S,
        STALE_AFTER_S,
    )
    while True:
        # Phase 1: drive forward progress (cheap, every tick).
        try:
            await asyncio.to_thread(_drive_active_books)
        except Exception:
            logger.exception("watchdog driver failed")
        # Phase 2: reap dead work + fail genuinely-stuck books.
        try:
            await asyncio.to_thread(_scan_once)
        except Exception:
            logger.exception("watchdog scan failed")
        # Phase 3: kill redelivered zombie tasks whose book/job is already
        # terminal — the automatic replacement for manual cancel-all purges.
        try:
            await asyncio.to_thread(_reap_zombie_tasks)
        except Exception:
            logger.exception("watchdog reaper failed")
        try:
            await asyncio.sleep(CHECK_INTERVAL_S)
        except asyncio.CancelledError:
            logger.info("watchdog cancelled")
            raise
