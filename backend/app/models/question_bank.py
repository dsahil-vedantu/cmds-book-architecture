from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class QuestionBank(Base):
    __tablename__ = "question_banks"

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    book_id: Mapped[UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    subject: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.String(32), default="pending", nullable=False)

    # Phase 1 — schema snapshot frozen at extraction time + linking summary stats
    schema_snapshot: Mapped[dict | None] = mapped_column(sa.JSON)
    linking_stats: Mapped[dict | None] = mapped_column(sa.JSON)

    # Stage 2 — per-block + total extraction rollup, surfaced in the Questions UI
    extraction_stats: Mapped[dict | None] = mapped_column(sa.JSON)
    last_error: Mapped[str | None] = mapped_column(sa.Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    questions = relationship(
        "Question",
        back_populates="bank",
        cascade="all, delete-orphan",
    )
