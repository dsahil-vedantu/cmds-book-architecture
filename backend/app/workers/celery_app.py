"""Celery app — only used when TASK_EXECUTOR=celery.

In local mode (default) tasks run inline via app.workers.runner. We still
create a stand-in ``celery_app`` object so the ``@celery_app.task`` decorator
in ``extract.py`` is a no-op pass-through that returns the wrapped function.
"""

from __future__ import annotations

from app.core.config import settings


class _NoopCeleryApp:
    """Minimal shim so module-level decorators work when Celery isn't installed."""

    conf = type("Conf", (), {"update": staticmethod(lambda **_: None)})()

    def task(self, *_args, **_kwargs):
        def decorator(fn):
            fn.delay = lambda *a, **kw: None  # type: ignore[attr-defined]
            return fn

        return decorator

    def send_task(self, *_a, **_kw):  # pragma: no cover — inline path never hits this
        raise RuntimeError("Celery is not available; set TASK_EXECUTOR=inline")


if settings.TASK_EXECUTOR == "celery":
    import os

    from celery import Celery  # type: ignore[import-not-found]

    # Hard/soft per-task limits — the backstop that auto-kills a genuinely hung
    # task and frees its slot WITHOUT a human/restart. Tightened from 30→20 min
    # so a true hang recovers faster, but kept generous enough that legitimate
    # long stages (big scanned books) finish well under it. Env-overridable so
    # an unusually large book can be given more headroom without a code change.
    _HARD_LIMIT_S = int(os.getenv("CELERY_TASK_TIME_LIMIT", str(60 * 20)))
    _SOFT_LIMIT_S = int(os.getenv("CELERY_TASK_SOFT_TIME_LIMIT", str(60 * 17)))
    # INVARIANT (see below): broker visibility_timeout MUST exceed the hard
    # limit, else a long-but-healthy task gets redelivered while still running.
    _VISIBILITY_S = max(60 * 35, _HARD_LIMIT_S + 60 * 5)

    # NOTE: no result backend configured. Our app tracks task state via Job
    # rows in Postgres (status/progress/error fields) — we never call
    # `.delay().get()` or use AsyncResult. Configuring a Redis result backend
    # caused "Retry limit exceeded while trying to reconnect to the Celery
    # result store backend" errors in the API process because send_task tried
    # to set up a result consumer over a flaky Redis pub/sub connection.
    celery_app: "Celery | _NoopCeleryApp" = Celery(
        "cmds",
        broker=settings.CELERY_BROKER_URL,
        # Every worker module that defines @celery_app.task functions must be
        # listed here so Celery imports them at startup and registers the
        # tasks in its registry. Missing a module = silent "task not found"
        # on dispatch.
        include=[
            # The orchestrator MUST be first/explicit: it defines
            # `coordinate_extraction` and `verify_dispatch`, the tasks that
            # drive every stage handoff. Previously it was only registered
            # as a transitive side-effect of importing extract.py, which is
            # timing-dependent — on a fresh worker (startup, max-tasks-per-
            # child respawn, redeploy) a coordinate_extraction message could
            # arrive BEFORE the task was registered → KeyError → Celery
            # silently drops the message → the book stalls at its current
            # stage forever. Listing it explicitly guarantees registration
            # at worker boot, deterministically.
            "app.workers.orchestrator",
            "app.workers.extract",
            "app.workers.questions",
            "app.workers.questions_v2",
            "app.workers.questions_v3",
            "app.workers.question_regen_v3",
            "app.workers.figures_tasks",
            "app.workers.qa",
        ],
    )
    celery_app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        # No result backend → no task_track_started (would require result store).
        task_ignore_result=True,
        task_time_limit=_HARD_LIMIT_S,
        task_soft_time_limit=_SOFT_LIMIT_S,
        worker_max_tasks_per_child=50,
        # Crash safety: don't ack until task fully completes.
        # If the worker is killed mid-task, the broker re-queues it automatically.
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        # One task at a time per worker slot — prevents slow tasks starving the queue.
        worker_prefetch_multiplier=1,
        # #4 — Redis broker visibility timeout. With acks_late, an in-flight
        # task's message stays "reserved" (invisible) until the worker acks OR
        # this timeout expires, at which point Redis REDELIVERS it. The
        # INVARIANT: visibility_timeout MUST exceed task_time_limit, otherwise
        # a long-but-healthy task gets redelivered while still running →
        # DUPLICATE execution. We set it just above the 30-min hard limit so:
        #   • no duplicate execution of a legitimately long task, and
        #   • a crashed worker's orphaned task is redelivered within ~35 min
        #     (broker-level backstop). The PRIMARY fast crash-recovery is the
        #     watchdog driver's dead-worker detection (~2 min via heartbeat);
        #     this is the belt-and-suspenders layer beneath it.
        broker_transport_options={"visibility_timeout": _VISIBILITY_S},
    )
else:
    celery_app = _NoopCeleryApp()
