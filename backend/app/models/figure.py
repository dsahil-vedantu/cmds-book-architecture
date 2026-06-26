from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Figure(Base):
    __tablename__ = "figures"

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    book_id: Mapped[UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    section_id: Mapped[str] = mapped_column(sa.String(256), nullable=False, index=True)
    # Phase 2 of canonical identity migration (CONTRACT.md §1). Nullable
    # during migration; section_id (string slug, misleadingly named) is
    # kept for legacy paths until Phase 5 cleanup renames/drops it.
    section_uuid: Mapped[UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("sections.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    figure_number: Mapped[str | None] = mapped_column(sa.Text)
    caption: Mapped[str | None] = mapped_column(sa.Text)
    description: Mapped[str | None] = mapped_column(sa.Text)
    image_url: Mapped[str | None] = mapped_column(sa.Text)
    page_number: Mapped[int | None] = mapped_column(sa.Integer)
    bounding_box: Mapped[dict | None] = mapped_column(sa.JSON)
    semantic_type: Mapped[str] = mapped_column(sa.String(32), default="other")
    tags: Mapped[list] = mapped_column(sa.JSON, default=list)
    status: Mapped[str] = mapped_column(sa.String(32), default="extracted")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # ----- Figures pipeline v2 additions (migration 0015) -----
    image_bytes: Mapped[bytes | None] = mapped_column(sa.LargeBinary, nullable=True)
    regen_image_bytes: Mapped[bytes | None] = mapped_column(sa.LargeBinary, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(sa.String(64), default="image/png")
    regen_version: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    regen_status: Mapped[str] = mapped_column(sa.String(32), default="none", nullable=False)
    figure_id_text: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    normalized_label: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    source_hash: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    regen_cache_key: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    context_hint: Mapped[str | None] = mapped_column(sa.String(32), nullable=True)
    regen_meta: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    # 0030 — body_type. Only meaningful when context_hint = "question":
    #   "question" → figure appears in the question stem (the prompt body)
    #   "solution" → figure appears in the worked solution (e.g. step diagram)
    #   NULL        → not applicable (theory figs leave this null;
    #                  context_hint alone identifies them)
    # Used by the question-figure embedder (F1/F2) to route into
    # Question.raw_text vs Question.solution_text.
    body_type: Mapped[str | None] = mapped_column(sa.String(16), nullable=True)
    # 0016 — Q5 approval workflow. Set when user clicks "Approve & move to
    # Regenerated"; cleared on Unapprove. ✨ Regenerated folder filters
    # to rows where approved_at IS NOT NULL.
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    book = relationship("Book", back_populates="figures")
    regenerations = relationship("FigureRegeneration", back_populates="figure", cascade="all, delete-orphan")
