"""Folder model — groups books (= "chapters" in the V-Studio UX) into a
user-named container.

A Folder is purely organisational metadata: it does not affect extraction,
regeneration, or any pipeline. Books may have ``folder_id = NULL`` (the
"unfiled" state), though the 0021 migration backfills all existing rows
into a default folder so the V-Studio library never has to render that
case for the legacy data.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Folder(Base):
    __tablename__ = "folders"

    id: Mapped[UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # Hex color string e.g. "#1A237E". Assigned randomly on create from a
    # fixed palette; surface in cards/list rows.
    color: Mapped[str] = mapped_column(String(9), nullable=False, default="#1A237E")
    # Subject tag (e.g. "Mathematics") — inherited by chapters inside.
    subject: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
