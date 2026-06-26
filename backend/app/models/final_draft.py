"""FinalDraft — Phase 3.2.

One active draft per book. Stores the user's authored chapter composition
as an ordered JSON list of items. Items are snapshots so user edits are
isolated from source extraction tables. Re-seeding pulls the latest Final
Merge state and replaces the items.

Item shape (each is a dict in the `items` list):

    {
      "id": "<stable-uuid>",            # for drag-drop reorder
      "type": "section_heading" | "block" | "figure" | "question" | "custom_text",
      "parent_section_id": str | null,  # informational; structure is the flat list order
      ...payload depending on type
    }

Payloads:
    section_heading: { section_id, title, level, regen: bool }
    block:           { block: <Block dict>, source_section_id }
    figure:          { figure: <EmbeddedFigure dict>, source_section_id }
    question:        { question: <FinalMergeQuestion dict>, source_section_id }
    custom_text:     { content: str (markdown allowed) }
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class FinalDraft(Base):
    __tablename__ = "final_drafts"

    id: Mapped[UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    book_id: Mapped[UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    items: Mapped[list] = mapped_column(sa.JSON, nullable=False, default=list)
    # draft | exporting | exported | failed
    status: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, default="draft", server_default="draft"
    )
    # Whether the latest seed used regenerated content. Stored so the
    # composer can show a clear badge to the user.
    prefer_regen: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        default=True,
        server_default=sa.text("1"),
    )
    # True once the user makes a manual edit (PATCH op) since the last seed.
    # While dirty, GET does NOT auto-reseed, so manual edits (delete/reorder/
    # edit) survive and reflect in Preview. An explicit reseed / merge-regen
    # clears it. Without this, the unconditional auto-reseed on every GET
    # wiped Composer edits before Preview could show them.
    is_dirty: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        default=False,
        server_default=sa.text("0"),
    )
    last_seeded_at: Mapped[datetime | None] = mapped_column(
        DateTime(), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(), nullable=False, server_default=sa.func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(),
        nullable=False,
        server_default=sa.func.current_timestamp(),
        onupdate=sa.func.current_timestamp(),
    )
