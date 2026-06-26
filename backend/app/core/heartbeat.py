"""Per-job heartbeat used by long-running workers.

A worker wraps an LLM call (or any blocking section) in
``with Heartbeat(job_uuid, base_msg, progress): ...``. Every ``interval``
seconds the heartbeat thread writes:

* ``job.last_heartbeat_at`` — read by the watchdog (app.core.watchdog) to
  decide a job is stuck and should be marked failed.
* ``job.message`` — appended with elapsed seconds so the UI shows progress.
* ``job.progress`` — kept at the supplied value during the call.

Failures inside the heartbeat thread are logged, never swallowed. A failing
heartbeat must not interrupt the actual work, but it must be visible.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models.job import Job

logger = logging.getLogger(__name__)

_engine = create_engine(
    settings.SYNC_DATABASE_URL,
    pool_pre_ping=True,
    pool_size=2, max_overflow=3, pool_timeout=10, pool_recycle=900,
)
_HeartbeatSession = sessionmaker(bind=_engine, class_=Session, autoflush=False)


class Heartbeat:
    def __init__(
        self,
        job_uuid: UUID | None,
        base_msg: str,
        progress: int,
        *,
        interval: float = 10.0,
    ) -> None:
        self._job_uuid = job_uuid
        self._base_msg = base_msg
        self._progress = progress
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._t0 = 0.0

    def __enter__(self) -> "Heartbeat":
        self._t0 = time.monotonic()
        if self._job_uuid is not None:
            # Beat once immediately so the watchdog clock starts now, not in
            # +interval seconds (otherwise a fast-failing call leaves stale ts).
            self._beat(elapsed=0)
            self._thread = threading.Thread(
                target=self._run, daemon=True, name="job-heartbeat"
            )
            self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            self._beat(elapsed=int(time.monotonic() - self._t0))

    def _beat(self, *, elapsed: int) -> None:
        try:
            with _HeartbeatSession() as s:
                job = s.get(Job, self._job_uuid)
                if job is None:
                    return
                job.message = (
                    f"{self._base_msg} — {elapsed}s elapsed"
                    if elapsed
                    else self._base_msg
                )
                job.progress = self._progress
                job.last_heartbeat_at = datetime.now(timezone.utc)
                s.commit()
        except Exception:
            # Surface heartbeat errors but never propagate — they must not
            # interrupt the actual extraction.
            logger.exception("heartbeat write failed for job %s", self._job_uuid)


class NullHeartbeat:
    """No-op when no job_uuid is bound."""

    def __enter__(self) -> "NullHeartbeat":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None
