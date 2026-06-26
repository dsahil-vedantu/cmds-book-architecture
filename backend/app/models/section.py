from __future__ import annotations

from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Section(Base):
    __tablename__ = "sections"
    __table_args__ = (UniqueConstraint("book_id", "section_id", name="uq_sections_book_section"),)

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    book_id: Mapped[UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # NOTE: widened to 256 in migration 0020. SQLite ignores width;
    # Postgres enforces it, and slug-style IDs for deeply-nested worked
    # examples (e.g. "...-finding-the-distance-...-example-8-38") can
    # exceed 64 chars.
    section_id: Mapped[str] = mapped_column(String(256), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[int | None] = mapped_column(Integer)
    bloom: Mapped[int | None] = mapped_column(Integer)
    tags: Mapped[list[str] | None] = mapped_column(sa.JSON)  # JSON list for cross-DB
    blocks: Mapped[list[dict]] = mapped_column(sa.JSON, nullable=False, default=list)
    source_chunk: Mapped[str | None] = mapped_column(Text)
    qc_local: Mapped[dict | None] = mapped_column(sa.JSON)
    qc_llm: Mapped[dict | None] = mapped_column(sa.JSON)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    book = relationship("Book", back_populates="sections")
