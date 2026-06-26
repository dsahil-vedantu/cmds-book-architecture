from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    book_id: UUID | None
    type: str
    status: str
    progress: int
    message: str | None
    error: str | None
    started_at: datetime | None
    finished_at: datetime | None
