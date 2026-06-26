from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Regeneration(Base):
    __tablename__ = "regenerations"

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    book_id: Mapped[UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    params: Mapped[dict] = mapped_column(sa.JSON, nullable=False)
    blocks_by_section: Mapped[dict] = mapped_column(sa.JSON, nullable=False)
    qc_drift: Mapped[dict | None] = mapped_column(sa.JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    book = relationship("Book", back_populates="regenerations")
