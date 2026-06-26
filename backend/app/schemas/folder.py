"""Pydantic schemas for the Folder API."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class FolderCreate(BaseModel):
    name: str
    subject: str | None = None
    color: str | None = None  # if omitted, server picks a random palette color


class FolderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    color: str
    subject: str | None = None
    created_at: datetime
    updated_at: datetime

    # Aggregate counts computed in the endpoint, not stored on the row.
    chapters: int = 0
    questions: int = 0
    figures: int = 0
    # Counts of books by status — drives the V-Studio status pill on cards.
    chapters_ready: int = 0
    chapters_processing: int = 0
    chapters_queued: int = 0
