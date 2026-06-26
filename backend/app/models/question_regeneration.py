from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class QuestionRegeneration(Base):
    __tablename__ = "question_regenerations"

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    bank_id: Mapped[UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("question_banks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    book_id: Mapped[UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_regen_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("question_regenerations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    label: Mapped[str | None] = mapped_column(sa.String(64))
    # scope: "bank" (whole-book regen) or "sections" (a subset)
    scope: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="bank", server_default="bank")
    section_refs: Mapped[list | None] = mapped_column(sa.JSON)
    custom_instructions: Mapped[str | None] = mapped_column(sa.Text)

    # R5 (2026-05-11) — per-regen v3 parameters. All nullable; the v3 worker
    # falls back to sane defaults when unset. The v2 worker ignores these
    # entirely so existing flows are unaffected.
    similarity_level: Mapped[str | None] = mapped_column(sa.String(64))
    count: Mapped[int | None] = mapped_column(sa.Integer)
    question_type: Mapped[str | None] = mapped_column(sa.String(64))
    priority_mode: Mapped[str | None] = mapped_column(sa.String(32))

    # status: pending | extracting | ready | failed | saved
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False, default="pending", server_default="pending")
    job_id: Mapped[UUID | None] = mapped_column(sa.Uuid(as_uuid=True), index=True)

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
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    questions = relationship(
        "Question",
        primaryjoin="QuestionRegeneration.id == foreign(Question.regen_id)",
        cascade="all, delete-orphan",
        single_parent=True,
    )
