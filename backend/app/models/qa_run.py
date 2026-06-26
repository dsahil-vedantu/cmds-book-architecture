from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class QARun(Base):
    """Immutable snapshot of one QA pass over a single section of a bank.

    One row per (bank_id, section_ref) per run. Older rows are kept for trend
    comparison across prompt versions.
    """

    __tablename__ = "question_qa_runs"

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    bank_id: Mapped[UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("question_banks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    section_ref: Mapped[str | None] = mapped_column(sa.String(128), nullable=True, index=True)

    # Completeness (Pillar A)
    expected_total: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    extracted_total: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    missed: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    hallucinated: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    # Fidelity (Pillar B)
    verbatim_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    paraphrased_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    not_verbatim_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    # Rolled-up bank/section score 0..1
    score: Mapped[float | None] = mapped_column(sa.Float)
    # Full failure list + any expected-vs-extracted diff detail (JSON)
    failures: Mapped[dict | list | None] = mapped_column(sa.JSON)

    model: Mapped[str | None] = mapped_column(sa.String(64))
    prompt_version: Mapped[str | None] = mapped_column(sa.String(32))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
