from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    book_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("books.id", ondelete="SET NULL"),
        index=True,
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Updated by the worker heartbeat. The watchdog uses this to fail jobs
    # whose worker has stopped responding (no progress for >5 min).
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    # When the Job row was first inserted (dispatch time). Needed by the
    # watchdog to detect "zombie queued" jobs — rows that have been in
    # `queued` for longer than QUEUE_STALE_AFTER_S without a Celery worker
    # picking them up. Without this column, a queued job has neither
    # `started_at` nor `last_heartbeat_at`, so its age can't be measured.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
        index=True,
    )
