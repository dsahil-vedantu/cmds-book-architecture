"""Rejected items captured during question extraction.

When the structural filter (or any future review pass) drops an item, we
keep the original payload here so the UI can show a "Restore" button.
Restoring promotes the row into ``questions`` as a normal Question.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class RejectedQuestion(Base):
    __tablename__ = "rejected_questions"

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
    )
    section_ref: Mapped[str | None] = mapped_column(sa.String(256), nullable=True, index=True)
    section_title: Mapped[str | None] = mapped_column(sa.Text)
    page_start: Mapped[int | None] = mapped_column(sa.Integer)
    page_end: Mapped[int | None] = mapped_column(sa.Integer)
    raw_text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    reject_reason: Mapped[str | None] = mapped_column(sa.Text)
    payload: Mapped[dict | None] = mapped_column(sa.JSON)

    # pending → user hasn't decided yet
    # restored → promoted into questions table (kept for audit; could be GC'd)
    # discarded → user explicitly said no, hide forever
    status: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default="pending", server_default="pending"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(sa.String(16))
