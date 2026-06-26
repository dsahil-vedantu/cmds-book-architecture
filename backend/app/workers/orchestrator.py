"""Post-schema extraction orchestrator.

State-driven Celery task that drives a book's post-schema lifecycle:

    schema done → theory → finalized → (questions + figures in parallel)
    → all terminal → ready/partial/failed

Idempotent — safe to dispatch multiple times. Each invocation:
  1. Acquires an atomic lock on the book row (10 min timeout)
  2. Reads current state (per-stage status fields)
  3. Decides the NEXT transition (one step)
  4. Dispatches the relevant worker(s) for that transition
  5. Releases the lock

Re-entrant — when a worker completes its work, it dispatches
coordinate_extraction again to step the state machine forward.

Pure logic — no LLM calls. Decides which worker to fire based on
state in DB. Workers do the actual Gemini work.

Replaces the fragile frontend-driven orchestration in
extractionPipeline.ts where every 2-second poll could miss state
transitions, /approve had no in-flight guard, and embedders ran twice
because theory_status="done" was committed BEFORE example_linker +
figure_embedder finished their tail work.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models.book import Book
from app.models.job import Job
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# Sync SQLAlchemy session for Celery tasks (mirror of the pattern in
# workers/extract.py). The shared async session in app.core.db isn't
# usable inside Celery's synchronous task functions.
_sync_engine = create_engine(
    settings.SYNC_DATABASE_URL,
    pool_pre_ping=True,
    pool_size=3, max_overflow=4, pool_timeout=15, pool_recycle=900,
)
SyncSession = sessionmaker(bind=_sync_engine, class_=Session, autoflush=False)


# Coordinator lock timeout. Watchdog should detect stale workers via
# heartbeat (5 min) and clean up Job rows; this lock auto-releases
# 5 min later as a safety net.
LOCK_TIMEOUT_MIN = 10

# ORCH Day 7 — auto-retry once per stage on failure. After this many
# retries, the coordinator finalizes the book as failed/partial instead
# of retrying further. Manual retry API endpoints (Day 10) reset the
# per-stage counter so a user-initiated retry can again use the slot.
MAX_AUTO_RETRIES = 1

# Per-book fair-queue cap on concurrent theory extractions.
# Global Gemini in-flight cap is 8 (see app/core/gemini_runtime.py). Each
# book's theory worker fans out 8 parallel section calls; running 3+ books
# at once causes contention where each book progresses at ~1/N solo speed.
# Capping concurrent THEORY books at 2 gives each active book its full
# 8-wide bandwidth. Total throughput is the same, but each book has a
# predictable wall-clock and queue position. Excess uploads stay in
# theory_status='pending' until a slot frees; the completing book's tail
# re-dispatches coordinate_extraction to wake the next pending book.
MAX_CONCURRENT_THEORY_BOOKS = 5


# Status sets — one source of truth.
_PENDING = "pending"
_RUNNING = "running"
_DONE = "done"
_FAILED = "failed"
_PARTIAL = "partial"
# SCHEMA Rebalance — accept-with-warnings schema outcome. NOT terminal-failed
# and NOT auto-advanceable: the book is waiting for the user to approve the
# warnings. The coordinator must never auto-advance a needs_review book.
_NEEDS_REVIEW = "needs_review"
_TERMINAL = frozenset({_DONE, _FAILED, _PARTIAL})


# ─── State machine ────────────────────────────────────────────────────


def _decide_next_action(book: Book, session=None) -> str:
    """Decide the next single transition based on current book state.

    Returns one of:
      "dispatch_analyse"    — schema pending, kick off the analyser
      "retry_analyse"       — schema failed, retries available
      "dispatch_theory"     — schema done, theory pending
      "dispatch_questions"  — theory finalized, questions pending (alone)
      "dispatch_figures"    — theory finalized, figures pending (alone)
      "dispatch_both"       — theory finalized, both pending
      "retry_theory"        — theory failed, retries available
      "retry_questions"     — questions failed, retries available
      "retry_figures"       — figures failed, retries available
      "finalize"            — all stages terminal, set book.status final
      "no_action"           — waiting for in-flight work / user approval
    """
    # Build Step 1 — handle the FULL lifecycle, schema included. The
    # coordinator is now the ONE entry point from upload → finalize.
    #
    # ─── Monotonic-forward state invariant (self-heal) ──────────────
    # The pipeline is strictly forward: a stage can never revert from a
    # terminal state back to pending while downstream stages have data.
    # If we observe `schema_status == pending` AND any downstream stage
    # has reached `done` / `needs_review` / `partial`, that's PROOF the
    # schema WAS done at some point (extract.py only runs on completed
    # schema). Some caller wrongly reset schema_status — auto-heal to
    # `done` rather than re-dispatch analyse (which would duplicate work
    # and corrupt the UI).
    #
    # Catches: /analyse endpoint with bug, reconciliation poke, manual
    # DB edit, double-clicked retry, future code paths we haven't
    # written yet. Single invariant enforcement at the dispatcher means
    # we don't have to patch every reset site individually.
    downstream_advanced = (
        book.theory_status in (_DONE, _NEEDS_REVIEW, _PARTIAL)
        or book.questions_status in (_DONE, _PARTIAL)
        or book.figures_status in (_DONE, _PARTIAL)
    )
    if book.schema_status == _PENDING and downstream_advanced:
        logger.warning(
            "orchestrator: state invariant violated book=%s — schema=pending "
            "but downstream advanced (theory=%s questions=%s figures=%s). "
            "Auto-healing schema → done (monotonic-forward).",
            book.id, book.theory_status, book.questions_status,
            book.figures_status,
        )
        # Restore the truth. We can't re-run schema-gen safely when
        # extracted data already exists; the schema in book.schema is
        # the authoritative one that produced that data.
        book.schema_status = _DONE
        if session is not None:
            session.commit()
        # Continue evaluating: the just-healed schema=done falls through
        # to the theory-pending/done checks below as normal.

    if book.schema_status == _PENDING:
        return "dispatch_analyse"
    if book.schema_status == _RUNNING:
        return "no_action"  # analyser in flight
    if book.schema_status == _FAILED:
        if (book.schema_retries or 0) < MAX_AUTO_RETRIES:
            return "retry_analyse"
        return "no_action"  # terminal-failed — surfaced to user, no auto-advance
    if book.schema_status == _NEEDS_REVIEW:
        # Auto-advance: validator flagged findings but the schema IS saved
        # and atomic (post race-fix). The schema in book.schema is the
        # FINAL schema — race-fix guarantees no later analyse can clobber
        # it. Theory worker reads that final schema via flatten_sections
        # and iterates every Cat-B section, so no section gets missed.
        #
        # Validator findings remain visible in book.schema_warnings for
        # human review; they no longer block extraction. User intent:
        # "schema finalized → next thing starts automatically."
        #
        # The genuinely-broken case (_FAILED above) still blocks — that's
        # where the schema is uninterpretable, not just imperfect. needs_review
        # specifically means "schema accepted with warnings" — extraction
        # safely proceeds.
        pass  # fall through to theory_status checks below
    if book.schema_status not in (_DONE, _NEEDS_REVIEW):
        # Unknown/unexpected schema status — be safe, don't act.
        # Both _DONE and _NEEDS_REVIEW are considered "schema finalized"
        # for downstream dispatch purposes (see needs_review fall-through
        # above). _FAILED was already handled at line 103.
        return "no_action"

    # Phase A — theory pending → dispatch (subject to per-book fair queue)
    if book.theory_status == _PENDING:
        # ── PER-BOOK FAIR QUEUE ──────────────────────────────────────
        # Global Gemini in-flight cap is 8 (gemini_runtime.py:42, can't
        # raise without Railway OOM). A book's theory worker fans out
        # 8 parallel section calls. When 3+ books run theory concurrently,
        # they fight for slots → each book progresses at ~1/N speed.
        #
        # Cap concurrent theory runs to MAX_CONCURRENT_THEORY_BOOKS so each
        # active book gets its full 8-wide bandwidth. Excess uploads stay
        # in theory_status='pending' until a slot frees. The completing
        # book's tail (extract.py end-of-job) calls
        # coordinate_extraction on the next pending book → wakes the queue.
        #
        # Observed need: 4-book concurrent upload had each book at 25%
        # speed; with queue gate, books would complete sequentially at
        # 100% speed (same total throughput, predictable per-book wall
        # clock, clearer UI semantics).
        # Use the caller's session if provided; otherwise open a short-lived
        # one. Caller is _coordinate_extraction which always passes session.
        # Direct-test callers (tests) get the fresh-session path.
        # Top-level imports to avoid the lazy-import class of bugs caught
        # earlier today (NameError surfaces only at runtime when the
        # branch fires — passes module-import sanity checks).
        from sqlalchemy import select as _select, func as _func  # noqa
        if session is not None:
            running_theory = session.execute(
                _select(_func.count(Book.id)).where(
                    Book.theory_status == _RUNNING,
                    Book.id != book.id,
                )
            ).scalar() or 0
        else:
            from app.core.db import SyncSession as _SS
            with _SS() as _s:
                running_theory = _s.execute(
                    _select(_func.count(Book.id)).where(
                        Book.theory_status == _RUNNING,
                        Book.id != book.id,
                    )
                ).scalar() or 0
        if running_theory >= MAX_CONCURRENT_THEORY_BOOKS:
            logger.info(
                "orchestrator: book=%s queued — %d books already running "
                "theory (cap=%d). Will dispatch when slot frees.",
                book.id, running_theory, MAX_CONCURRENT_THEORY_BOOKS,
            )
            return "no_action"
        return "dispatch_theory"

    # Phase B — theory still running → wait
    if book.theory_status == _RUNNING:
        return "no_action"

    # Phase C — theory failed → retry once (Day 7) or finalize
    if book.theory_status == _FAILED:
        if (book.theory_retries or 0) < MAX_AUTO_RETRIES:
            return "retry_theory"
        return "finalize"

    # Phase D — theory done but tail (linker+embedder) not yet finished
    if book.theory_status == _DONE and book.theory_finalized_at is None:
        return "no_action"

    # Phase E — theory finalized → decide on Q + Fig
    if book.theory_finalized_at is not None:
        q_pending = book.questions_status == _PENDING
        f_pending = book.figures_status == _PENDING
        q_failed = book.questions_status == _FAILED
        f_failed = book.figures_status == _FAILED

        # Retry failed stages first (one at a time so we don't compound
        # transient errors). Day 7 budget: MAX_AUTO_RETRIES per stage.
        if q_failed and (book.questions_retries or 0) < MAX_AUTO_RETRIES:
            return "retry_questions"
        if f_failed and (book.figures_retries or 0) < MAX_AUTO_RETRIES:
            return "retry_figures"

        if q_pending and f_pending:
            return "dispatch_both"
        if q_pending:
            return "dispatch_questions"
        if f_pending:
            return "dispatch_figures"

        # Neither pending — both running or terminal
        if (book.questions_status in _TERMINAL
                and book.figures_status in _TERMINAL):
            return "finalize"

    return "no_action"


# ─── Atomic stage transitions (CAS) ───────────────────────────────────
#
# Every stage_status transition uses an atomic compare-and-set (UPDATE
# ... WHERE) so concurrent actors can't race on it. The single rule
# across the whole pipeline:
#
#     If you're about to write stage_status, you MUST check that the
#     current value is what you expect. If it isn't, drop your write —
#     someone else got there first.
#
# This closes both classes of races we hit historically:
#   - Two dispatchers each create a worker (because both saw "pending")
#   - The losing duplicate's failure tail overwrites the winning sibling's
#     successful state (because the tail blindly wrote "failed")


_STAGE_COLS = {
    "schema":    Book.schema_status,
    "theory":    Book.theory_status,
    "questions": Book.questions_status,
    "figures":   Book.figures_status,
}


def cas_set_stage(
    session,
    book_uuid: UUID,
    stage: str,
    new_value: str,
    from_states: tuple[str, ...],
) -> bool:
    """Atomically transition a stage's status. Returns True iff we won.

    `stage` is one of "schema" / "theory" / "questions" / "figures".
    `from_states` are the values we expect the column to currently have;
    if it's anything else, our update is a no-op and we return False —
    the caller should drop whatever follow-up work they were planning.

    Commits inside so the write lands atomically. Callers using the
    in-memory Book object should refresh it after this returns True
    (their `book.<stage>_status` will be stale otherwise).
    """
    col = _STAGE_COLS[stage]
    result = session.execute(
        sa.update(Book)
        .where(Book.id == book_uuid)
        .where(col.in_(from_states))
        .values({col.key: new_value})
    )
    session.commit()
    won = result.rowcount > 0
    if not won:
        logger.info(
            "cas_set_stage: lost race book=%s stage=%s want=%s from=%s",
            book_uuid, stage, new_value, from_states,
        )
    return won


# ─── Lock primitives (atomic via UPDATE WHERE) ───────────────────────


def _try_acquire_lock(session, book_uuid: UUID) -> bool:
    """Atomically acquire the orchestrator lock for this book.

    Returns True if we got the lock (and the row's extraction_lock_at
    is now updated). False if another coordinator already holds it
    AND the lock is still fresh.

    Uses an atomic UPDATE with WHERE so concurrent racing coordinators
    will see exactly one succeed. The other sees rowcount=0 and exits.
    """
    cutoff = datetime.utcnow() - timedelta(minutes=LOCK_TIMEOUT_MIN)
    result = session.execute(
        sa.update(Book)
        .where(Book.id == book_uuid)
        .where(sa.or_(
            Book.extraction_lock_at.is_(None),
            Book.extraction_lock_at < cutoff,
        ))
        .values(extraction_lock_at=datetime.utcnow())
    )
    session.commit()
    return result.rowcount > 0


def _release_lock(session, book_uuid: UUID) -> None:
    """Release the orchestrator lock. Idempotent."""
    session.execute(
        sa.update(Book)
        .where(Book.id == book_uuid)
        .values(extraction_lock_at=None)
    )
    session.commit()


# ─── Dispatchers (create Job rows + dispatch worker tasks) ───────────


def _reset_recovery_attempts(session, book: Book) -> None:
    """Reset the reconciler's recovery counter on genuine forward progress.

    Called by every dispatcher AFTER it wins the CAS into _RUNNING — i.e.
    a stage just transitioned pending/failed → running and real work is
    being dispatched. This means a book that recovered cleanly via the
    reconciler is no longer carrying old recovery_attempts, so a later
    legitimate stall gets the full MAX_RECOVERY_ATTEMPTS budget again
    rather than being wrongly capped. Idempotent / cheap (no-op when
    already 0). Caller commits as part of its own dispatch commit.
    """
    if (book.recovery_attempts or 0) != 0:
        book.recovery_attempts = 0


def _new_job(session, book_uuid: UUID, job_type: str) -> UUID:
    """Create a Job row, commit, return its UUID."""
    job = Job(book_id=book_uuid, type=job_type, status="queued", progress=0)
    session.add(job)
    session.flush()
    job_id = job.id
    session.commit()
    return job_id


def _dispatch_theory(session, book: Book) -> None:
    """Dispatch theory worker (extract_book).

    Atomic guard via cas_set_stage — if a sibling coordinator already
    moved theory_status out of {pending, failed}, we drop this dispatch.
    """
    if not cas_set_stage(
        session, book.id, "theory", _RUNNING, from_states=(_PENDING, _FAILED),
    ):
        return
    session.refresh(book)
    _reset_recovery_attempts(session, book)
    job_id = _new_job(session, book.id, "extract")
    book.status = "extracting"
    session.commit()
    from datetime import datetime, timezone
    from app.workers.runner import dispatch, dispatch_after
    dispatch("extract_book", str(book.id), str(job_id), task_id=str(job_id))
    # Self-verify: if the dispatch was lost, this re-fires the coordinator
    # 60s later. Worker-side CAS makes duplicate dispatches a no-op.
    dispatch_after(
        "verify_dispatch", _VERIFY_DELAY_S,
        str(book.id), "theory", datetime.now(timezone.utc).isoformat(),
    )
    logger.info(
        "orchestrator: dispatched extract_book book=%s job=%s",
        book.id, job_id,
    )


def _dispatch_analyse(session, book: Book) -> UUID | None:
    """Dispatch the schema analyser (analyse_book).

    Atomic guard via cas_set_stage. If schema_status is already running
    or terminal, returns None — the API endpoint should map that to 409.
    Otherwise returns the new job_id.

    All callers (API endpoint, upload auto-dispatch, manual retry) go
    through this function so the atomic guarantee applies to every
    analyse dispatch site.
    """
    if not cas_set_stage(
        session, book.id, "schema", _RUNNING, from_states=(_PENDING, _FAILED),
    ):
        return None
    session.refresh(book)
    _reset_recovery_attempts(session, book)
    job_id = _new_job(session, book.id, "analyse")
    book.status = "analysing"
    session.commit()
    from app.workers.runner import dispatch
    dispatch("analyse_book", str(book.id), str(job_id), task_id=str(job_id))
    logger.info(
        "orchestrator: dispatched analyse_book book=%s job=%s",
        book.id, job_id,
    )
    return job_id


def _dispatch_questions(session, book: Book) -> None:
    """Dispatch the questions worker.

    The questions worker takes 3 args: (book_id, bank_id, job_id). We
    have to create a QuestionBank row first — this mirrors the
    /question-banks API endpoint's pattern (see api/question_banks.py).

    Prior pending/extracting banks for this book are marked failed
    ("Superseded") so the bank list stays clean across retries.
    """
    from app.models.question_bank import QuestionBank

    # Atomic CAS FIRST — drop dispatch if a sibling already moved the stage.
    # MUST run before any DB writes so a CAS-loser doesn't leak a
    # QuestionBank row or clobber the winner's pending bank list.
    if not cas_set_stage(
        session, book.id, "questions", _RUNNING, from_states=(_PENDING, _FAILED),
    ):
        return

    # Supersede any prior pending/extracting banks (orphans from
    # earlier retries that never finished).
    session.execute(
        sa.update(QuestionBank)
        .where(QuestionBank.book_id == book.id)
        .where(QuestionBank.status.in_(["pending", "extracting"]))
        .values(
            status="failed",
            last_error="Superseded by orchestrator re-dispatch",
        )
    )

    # Create the new QuestionBank row the worker writes into.
    bank = QuestionBank(
        book_id=book.id,
        title=book.title,
        subject=book.subject,
        status="pending",
    )
    session.add(bank)
    session.flush()
    bank_id = bank.id

    session.refresh(book)
    _reset_recovery_attempts(session, book)
    job_id = _new_job(session, book.id, "extract_questions")
    book.status = "extracting"
    session.commit()

    from datetime import datetime, timezone
    from app.workers.runner import dispatch, dispatch_after
    dispatch(
        "extract_questions_v3",
        str(book.id), str(bank_id), str(job_id),
        task_id=str(job_id),
    )
    dispatch_after(
        "verify_dispatch", _VERIFY_DELAY_S,
        str(book.id), "questions", datetime.now(timezone.utc).isoformat(),
    )
    logger.info(
        "orchestrator: dispatched extract_questions_v3 for book=%s "
        "bank=%s job=%s",
        book.id, bank_id, job_id,
    )


def _dispatch_figures(session, book: Book) -> None:
    if not cas_set_stage(
        session, book.id, "figures", _RUNNING, from_states=(_PENDING, _FAILED),
    ):
        return
    session.refresh(book)
    _reset_recovery_attempts(session, book)
    job_id = _new_job(session, book.id, "extract_figures")
    book.status = "extracting"
    session.commit()
    from datetime import datetime, timezone
    from app.workers.runner import dispatch, dispatch_after
    dispatch("extract_figures_v2", str(book.id), str(job_id), task_id=str(job_id))
    dispatch_after(
        "verify_dispatch", _VERIFY_DELAY_S,
        str(book.id), "figures", datetime.now(timezone.utc).isoformat(),
    )
    logger.info(
        "orchestrator: dispatched extract_figures_v2 book=%s job=%s",
        book.id, job_id,
    )


def _finalize(session, book: Book) -> None:
    """Derive the terminal book.status from per-stage fields and commit."""
    from app.services.book_status import derive_book_status
    book.status = derive_book_status(book)
    session.commit()
    logger.info(
        "orchestrator: finalized book=%s status=%s "
        "(theory=%s questions=%s figures=%s)",
        book.id, book.status, book.theory_status,
        book.questions_status, book.figures_status,
    )


# ─── Retry handlers (ORCH Day 7) ──────────────────────────────────────


def _retry_analyse(session, book: Book) -> None:
    """Auto-retry schema (analyse) after a failed first attempt.

    Reset schema_status failed → pending via CAS (so we don't clobber a
    sibling that already moved it), bump the counter, then dispatch the
    analyser. _dispatch_analyse itself does the pending → running CAS.
    """
    if not cas_set_stage(
        session, book.id, "schema", _PENDING, from_states=(_FAILED,),
    ):
        return  # sibling already moved it — drop this retry
    book.schema_retries = (book.schema_retries or 0) + 1
    session.commit()
    session.refresh(book)
    logger.warning(
        "orchestrator: AUTO-RETRY schema (attempt %d of %d) for book=%s",
        book.schema_retries + 1, MAX_AUTO_RETRIES + 1, book.id,
    )
    _dispatch_analyse(session, book)


def _retry_theory(session, book: Book) -> None:
    """Auto-retry theory after a failed first attempt.

    Reset theory_status to "pending", clear theory_finalized_at (so the
    next coordinator pass doesn't skip retry to Q/Fig), bump the
    counter, then dispatch extract_book again.
    """
    book.theory_retries = (book.theory_retries or 0) + 1
    book.theory_status = _PENDING
    book.theory_finalized_at = None
    session.commit()
    logger.warning(
        "orchestrator: AUTO-RETRY theory (attempt %d of %d) for book=%s",
        book.theory_retries + 1, MAX_AUTO_RETRIES + 1, book.id,
    )
    _dispatch_theory(session, book)


def _retry_questions(session, book: Book) -> None:
    """Auto-retry questions after a failed first attempt."""
    book.questions_retries = (book.questions_retries or 0) + 1
    book.questions_status = _PENDING
    session.commit()
    logger.warning(
        "orchestrator: AUTO-RETRY questions (attempt %d of %d) for book=%s",
        book.questions_retries + 1, MAX_AUTO_RETRIES + 1, book.id,
    )
    _dispatch_questions(session, book)


def _retry_figures(session, book: Book) -> None:
    """Auto-retry figures after a failed first attempt."""
    book.figures_retries = (book.figures_retries or 0) + 1
    book.figures_status = _PENDING
    session.commit()
    logger.warning(
        "orchestrator: AUTO-RETRY figures (attempt %d of %d) for book=%s",
        book.figures_retries + 1, MAX_AUTO_RETRIES + 1, book.id,
    )
    _dispatch_figures(session, book)


# ─── Public task ──────────────────────────────────────────────────────


def _coordinate_extraction(book_id: str) -> dict:
    """Step the post-schema extraction state machine forward by ONE transition.

    Idempotent. Safe to dispatch repeatedly. Worker tails of
    extract_book, extract_questions_v3, extract_figures_v2 re-dispatch
    this task at their completion to step the state forward.

    No LLM calls. Pure DB state inspection + Celery dispatch.

    Sync entrypoint registered with both the inline dispatch table
    (workers/runner.py) and the Celery task wrapper below.
    """
    try:
        book_uuid = UUID(book_id)
    except (ValueError, TypeError):
        logger.warning("orchestrator: invalid book_id %r", book_id)
        return {"ok": False, "reason": "invalid_book_id"}

    with SyncSession() as session:
        # Atomic lock acquisition
        if not _try_acquire_lock(session, book_uuid):
            logger.info(
                "orchestrator: book %s lock held by another coordinator, exiting",
                book_id,
            )
            return {"ok": True, "reason": "lock_held"}

        try:
            book = session.get(Book, book_uuid)
            if book is None:
                logger.warning("orchestrator: book %s not found", book_id)
                return {"ok": False, "reason": "book_not_found"}

            action = _decide_next_action(book, session=session)
            logger.info(
                "orchestrator: book=%s schema=%s theory=%s(finalized=%s) "
                "questions=%s figures=%s → action=%s",
                book.id, book.schema_status, book.theory_status,
                book.theory_finalized_at is not None,
                book.questions_status, book.figures_status, action,
            )

            if action == "dispatch_analyse":
                _dispatch_analyse(session, book)
            elif action == "retry_analyse":
                _retry_analyse(session, book)
            elif action == "dispatch_theory":
                _dispatch_theory(session, book)
            elif action == "dispatch_questions":
                _dispatch_questions(session, book)
            elif action == "dispatch_figures":
                _dispatch_figures(session, book)
            elif action == "dispatch_both":
                _dispatch_questions(session, book)
                _dispatch_figures(session, book)
            elif action == "retry_theory":
                _retry_theory(session, book)
            elif action == "retry_questions":
                _retry_questions(session, book)
            elif action == "retry_figures":
                _retry_figures(session, book)
            elif action == "finalize":
                _finalize(session, book)
            # action == "no_action" → no-op

            return {"ok": True, "action": action}
        except Exception as e:
            logger.exception("orchestrator: book=%s crashed: %s", book_id, e)
            return {"ok": False, "reason": "exception", "error": str(e)[:200]}
        finally:
            # Always release the lock so retries can proceed
            _release_lock(session, book_uuid)


# ─── Task wiring ──────────────────────────────────────────────────────


# Celery wrapper — Celery binds `self` as first arg. Delegates to the
# plain sync function so inline mode and Celery mode share one
# implementation (matches the pattern in extract.py / questions_v3.py).
@celery_app.task(name="coordinate_extraction", bind=True)
def coordinate_extraction_task(self, book_id: str) -> dict:
    return _coordinate_extraction(book_id)


# ───────────────────────────────────────────────────────────────────
# Tier 2 self-verifying dispatch — see watchdog.py header for context.
#
# After every stage dispatch (_dispatch_theory / _dispatch_questions /
# _dispatch_figures), the orchestrator schedules a verify_dispatch task
# to fire 60s later. It re-checks the book's stage status:
#
#   • If the stage advanced past `pending`  → worker picked it up, no-op.
#   • If a fresh Job row exists for the stage → worker is about to start,
#     no-op (avoid racing the worker's CAS).
#   • Otherwise → the dispatch was lost. Re-dispatch the coordinator
#     (which will atomically re-fire the stage). Bounded to 2 retries to
#     avoid infinite loops; the watchdog catches anything beyond that.
#
# Idempotency: this is safe to call multiple times for the same dispatch
# because workers use atomic CAS (UPDATE … WHERE stage_status='pending')
# on entry — only the first arriving worker for a stage flips it to
# `running` and proceeds. All subsequent dispatches see status!='pending'
# at the CAS step and exit immediately, doing zero work.
# ───────────────────────────────────────────────────────────────────

_VERIFY_MAX_ATTEMPTS = 3            # initial + 2 re-verifies
_VERIFY_DELAY_S = 60.0              # 60s after dispatch
_FRESH_JOB_WINDOW_S = 90            # a Job created in the last 90s = "fresh"

# Per-stage mapping: stage name → (status attribute, dispatched task name)
_STAGE_TO_TASK = {
    "theory": ("theory_status", "extract_book"),
    "questions": ("questions_status", "extract_questions_v3"),
    "figures": ("figures_status", "extract_figures_v2"),
}


def _verify_dispatch(book_id: str, stage: str, dispatched_at_iso: str,
                     attempt: int = 1) -> dict:
    """Verify that a stage dispatch was picked up by a worker.

    Called 60s after `_dispatch_theory/_questions/_figures` fires its
    Celery message. If the stage's status is still `pending` AND no
    fresh Job row exists, the dispatch was lost — re-fire the coordinator
    so the state machine can dispatch the stage again. Idempotent: see
    the CAS guard at each worker's entry point — duplicate dispatches
    are safe.

    Returns a dict for logging; does not raise.
    """
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select
    from app.models.book import Book
    from app.models.job import Job

    if stage not in _STAGE_TO_TASK:
        return {"ok": False, "error": f"unknown stage: {stage}"}
    status_attr, task_type = _STAGE_TO_TASK[stage]

    try:
        dispatched_at = datetime.fromisoformat(dispatched_at_iso)
    except (TypeError, ValueError):
        return {"ok": False, "error": "bad dispatched_at"}
    if dispatched_at.tzinfo is None:
        dispatched_at = dispatched_at.replace(tzinfo=timezone.utc)

    with SyncSession() as session:
        book = session.get(Book, book_id)
        if book is None:
            return {"ok": False, "error": "book vanished"}

        stage_status = getattr(book, status_attr, None)

        # ─── Already advanced — worker picked it up ─────────────
        if stage_status != _PENDING:
            logger.info(
                "verify_dispatch: book=%s stage=%s status=%s (advanced) — no-op",
                book_id, stage, stage_status,
            )
            return {"ok": True, "no_op": True, "reason": f"status={stage_status}"}

        # ─── Fresh Job exists — worker about to start ─────────────
        now = datetime.now(timezone.utc)
        fresh_cutoff = now - timedelta(seconds=_FRESH_JOB_WINDOW_S)
        fresh_job = session.execute(
            select(Job).where(
                Job.book_id == book_id,
                Job.type == task_type,
                Job.created_at > fresh_cutoff,
            ).order_by(Job.created_at.desc()).limit(1)
        ).scalars().first()
        if fresh_job is not None:
            logger.info(
                "verify_dispatch: book=%s stage=%s fresh_job=%s — no-op",
                book_id, stage, fresh_job.id,
            )
            return {"ok": True, "no_op": True, "reason": "fresh_job"}

        # ─── Bounded retries — give up after 3 attempts ─────────
        if attempt >= _VERIFY_MAX_ATTEMPTS:
            logger.warning(
                "verify_dispatch: book=%s stage=%s attempt=%d — giving up "
                "(watchdog will catch via zombie-job sweep)",
                book_id, stage, attempt,
            )
            return {"ok": True, "gave_up": True}

        # ─── Genuinely dropped — re-fire the coordinator ────────
        logger.warning(
            "verify_dispatch: book=%s stage=%s status=pending, no fresh job "
            "after %ds → re-dispatching coordinator (attempt %d/%d)",
            book_id, stage, _FRESH_JOB_WINDOW_S, attempt, _VERIFY_MAX_ATTEMPTS,
        )

    # Re-fire OUTSIDE the session block.
    from app.workers.runner import dispatch, dispatch_after
    dispatch("coordinate_extraction", book_id)
    # Schedule the next verify pass; CAS at the worker entry keeps this safe.
    dispatch_after(
        "verify_dispatch", _VERIFY_DELAY_S,
        book_id, stage,
        datetime.now(timezone.utc).isoformat(),
        attempt + 1,
    )
    return {"ok": True, "redispatched": True, "attempt": attempt}


@celery_app.task(name="verify_dispatch", bind=True)
def verify_dispatch_task(
    self, book_id: str, stage: str, dispatched_at_iso: str, attempt: int = 1,
) -> dict:
    return _verify_dispatch(book_id, stage, dispatched_at_iso, attempt)


# Inline-mode registration — runner.dispatch("coordinate_extraction", ...)
# resolves to this. Without it, inline mode (default when Redis is a
# stub) raises "Inline task not registered".
from app.workers.runner import register as register_task  # noqa: E402

register_task("coordinate_extraction", _coordinate_extraction)
register_task("verify_dispatch", _verify_dispatch)
