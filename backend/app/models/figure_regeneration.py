from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class FigureRegeneration(Base):
    __tablename__ = "figure_regenerations"

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    book_id: Mapped[UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    figure_id: Mapped[UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("figures.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    section_id: Mapped[str] = mapped_column(sa.String(256), nullable=False)
    image_url: Mapped[str | None] = mapped_column(sa.Text)
    style_params: Mapped[dict | None] = mapped_column(sa.JSON)
    model_used: Mapped[str | None] = mapped_column(sa.String(128))
    status: Mapped[str] = mapped_column(sa.String(32), default="completed")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    book = relationship("Book", back_populates="figure_regenerations")
    figure = relationship("Figure", back_populates="regenerations")
