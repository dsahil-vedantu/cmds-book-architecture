from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Question(Base):
    __tablename__ = "questions"

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
    section_ref: Mapped[str | None] = mapped_column(sa.String(256), nullable=True, index=True)
    # Phase 2 of canonical identity migration (CONTRACT.md §1). Nullable
    # during migration; Phase 4 will require it for new uploads. Joins
    # use this UUID FK; section_ref is kept for legacy paths + display.
    section_uuid: Mapped[UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("sections.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    section_title: Mapped[str | None] = mapped_column(sa.Text)
    page_start: Mapped[int | None] = mapped_column(sa.Integer)
    page_end: Mapped[int | None] = mapped_column(sa.Integer)
    raw_text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    qc_local: Mapped[dict | None] = mapped_column(sa.JSON)
    attempts: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(32), default="pending", nullable=False)

    # Phase 1 linking fields — populated by the 7-rule cascade
    excluded_block_ref: Mapped[str] = mapped_column(
        sa.String(255), nullable=False, default="", server_default=""
    )
    excluded_block_index: Mapped[int | None] = mapped_column(sa.Integer)
    excluded_block_page_start: Mapped[int | None] = mapped_column(sa.Integer)
    excluded_block_page_end: Mapped[int | None] = mapped_column(sa.Integer)
    link_method: Mapped[str | None] = mapped_column(sa.String(32))
    link_confidence: Mapped[float | None] = mapped_column(sa.Float)
    link_rule_trace: Mapped[list | None] = mapped_column(sa.JSON)
    linked_by: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default="system", server_default="system"
    )
    linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Stage 2 OCR metadata — verbatim tags captured during extraction
    question_number: Mapped[str | None] = mapped_column(sa.String(32))
    exercise_ref: Mapped[str | None] = mapped_column(sa.String(128))
    chapter_ref: Mapped[str | None] = mapped_column(sa.String(256))
    sub_part: Mapped[str | None] = mapped_column(sa.String(8))
    question_type: Mapped[str | None] = mapped_column(sa.String(32))
    has_options: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    solution_text: Mapped[str | None] = mapped_column(sa.Text)
    has_solution: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    # Per-block self-check total from the OCR pass (denormalised for quick rollups)
    identified_total: Mapped[int | None] = mapped_column(sa.Integer)

    # Kind of question as literally printed on the page (for folder grouping):
    # exercise | example | problem | try_it | review | mcq | other
    kind: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default="exercise", server_default="exercise"
    )

    # QA agent output — deterministic fidelity + LLM test results
    # qc_status: pending | passed | flagged | failed
    qc_status: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default="pending", server_default="pending"
    )
    qc_score: Mapped[float | None] = mapped_column(sa.Float)
    qc_tests: Mapped[dict | list | None] = mapped_column(sa.JSON)

    # Regeneration tracking — original rows have regen_id IS NULL.
    regen_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("question_regenerations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    source_regen_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("question_regenerations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # 0014 (2026-05-11) — for regen rows, points to the original Question
    # (regen_id IS NULL) that was used as the source. Lets the UI group N
    # variants under their specific source instead of mixing them at the
    # section level. Always NULL on original rows.
    source_question_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        nullable=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Soft-hide via UI. Hidden questions stay in the DB and exports but the
    # default question listing skips them. Toggle with PATCH /questions/{id}.
    is_hidden: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )

    bank = relationship("QuestionBank", back_populates="questions")
