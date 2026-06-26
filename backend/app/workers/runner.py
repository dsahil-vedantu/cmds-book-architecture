"""Task dispatcher — picks inline (threaded) or Celery based on settings.

All callers use ``dispatch(name, *args)`` instead of ``task.delay(...)``. The
dispatcher looks up the task function and either runs it in a daemon thread
(inline) or enqueues it on Celery.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from app.core.config import settings

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, Callable[..., Any]] = {}


def register(name: str, fn: Callable[..., Any]) -> None:
    _REGISTRY[name] = fn


def dispatch(name: str, *args: Any, task_id: str | None = None, **kwargs: Any) -> None:
    """Fire-and-forget task dispatch.

    - In ``inline`` mode: runs ``fn(*args, **kwargs)`` in a daemon thread so the
      HTTP request returns immediately. Progress is still visible because tasks
      update Job rows as they run.
    - In ``celery`` mode: enqueues via ``celery_app.send_task(name, args, kwargs)``.

    ``task_id`` — when set, the Celery task is given this explicit id. We pass
    the **Job UUID** here so the Celery task id == the Job id. That's what makes
    a running task killable: ``revoke_task(job_id)`` can target the exact task
    by id without needing a separate ``celery_task_id`` column. Ignored inline
    (daemon threads can't be killed — local dev only).
    """
    fn = _REGISTRY.get(name)
    if settings.TASK_EXECUTOR == "inline":
        if fn is None:
            raise RuntimeError(f"Inline task not registered: {name}")

        def target() -> None:
            try:
                fn(*args, **kwargs)
            except Exception:
                logger.exception("Inline task %s raised", name)

        thread = threading.Thread(target=target, daemon=True, name=f"task-{name}")
        thread.start()
        return

    # Celery path
    from app.workers.celery_app import celery_app

    celery_app.send_task(name, args=list(args), kwargs=kwargs, task_id=task_id)


def purge_and_kill_all() -> dict:
    """Nuke the worker: drop ALL queued tasks and SIGTERM ALL running ones.

    This is the sledgehammer behind cancel-all, and it fixes the "every upload
    just queues, never starts" trap:

    Celery is configured for crash-safety (task_acks_late +
    task_reject_on_worker_lost). So on every container restart, Redis
    REDELIVERS every task that was in-flight at restart time. Those redelivered
    tasks belong to books that already failed/were cancelled, but they re-run
    their full (minutes-long) extraction anyway and saturate every worker slot
    — so a freshly uploaded book's schema task sits queued behind them forever.

      • purge()  → clears the queued backlog (the zombies not yet started)
      • revoke(active, terminate) → SIGTERMs the zombies already running

    No-op inline. Never raises — best-effort cleanup must not break the request.
    Returns {purged, killed} counts.
    """
    if settings.TASK_EXECUTOR != "celery":
        return {"purged": 0, "killed": 0}
    from app.workers.celery_app import celery_app

    purged = 0
    killed = 0
    try:
        purged = celery_app.control.purge() or 0
    except Exception:  # pragma: no cover — best-effort
        logger.warning("purge_and_kill_all: purge failed", exc_info=True)
    try:
        active = celery_app.control.inspect(timeout=5).active() or {}
        ids = [t.get("id") for tasks in active.values() for t in tasks if t.get("id")]
        if ids:
            celery_app.control.revoke(ids, terminate=True, signal="SIGTERM")
            killed = len(ids)
    except Exception:  # pragma: no cover — best-effort
        logger.warning("purge_and_kill_all: revoke-active failed", exc_info=True)
    logger.info("purge_and_kill_all: purged=%s killed=%s", purged, killed)
    return {"purged": purged, "killed": killed}


def revoke_task(job_id: Any) -> bool:
    """Actually KILL a running/queued Celery task by id (== Job id).

    This is the missing piece that made ``cancel-all`` cosmetic: marking a Job
    ``cancelled`` in Postgres never told the worker to stop. ``revoke`` with
    ``terminate=True`` sends SIGTERM to the worker child executing that task, so
    the slot frees immediately — no backend restart required.

    No-op in inline mode (daemon threads are unkillable). Returns True if a
    revoke broadcast was sent, False otherwise. Never raises — cancellation is
    best-effort and must not break the request.
    """
    if settings.TASK_EXECUTOR != "celery":
        return False
    try:
        from app.workers.celery_app import celery_app

        # terminate=True → kill the executing task's child process now.
        # signal=SIGTERM → our tasks get a chance to unwind cleanly; Celery
        # escalates to SIGKILL if the child ignores it.
        celery_app.control.revoke(str(job_id), terminate=True, signal="SIGTERM")
        return True
    except Exception:  # pragma: no cover — best-effort
        logger.warning("revoke_task failed for %s", job_id, exc_info=True)
        return False


def dispatch_after(name: str, delay_s: float, *args: Any, **kwargs: Any) -> None:
    """Schedule a task to run after ``delay_s`` seconds.

    Used for self-verifying dispatches: after firing a stage worker, we schedule
    a verify task ``delay_s`` later to confirm the stage actually picked up. If
    not, the verify re-dispatches. See ``verify_dispatch`` in orchestrator.py.

    - Inline mode: ``threading.Timer`` fires the registered function on a daemon
      thread after the delay.
    - Celery mode: ``send_task(countdown=delay_s)`` — Celery natively supports
      delayed delivery via its broker.
    """
    if settings.TASK_EXECUTOR == "inline":
        fn = _REGISTRY.get(name)
        if fn is None:
            raise RuntimeError(f"Inline task not registered: {name}")

        def target() -> None:
            try:
                fn(*args, **kwargs)
            except Exception:
                logger.exception("Inline scheduled task %s raised", name)

        timer = threading.Timer(delay_s, target)
        timer.daemon = True
        timer.start()
        return

    from app.workers.celery_app import celery_app

    celery_app.send_task(
        name, args=list(args), kwargs=kwargs, countdown=delay_s,
    )
