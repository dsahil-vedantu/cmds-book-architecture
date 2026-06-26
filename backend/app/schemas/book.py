from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class BookBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    title: str
    subject: str | None = None


class BookCreate(BookBase):
    pass


class BookOut(BookBase):
    id: UUID
    folder_id: UUID | None = None
    pdf_url: str | None = None
    schema_: dict[str, Any] | None = None  # "schema" is reserved by pydantic
    analyser: dict[str, Any] | None = None
    status: str
    # ORCH Day 12.5 — per-stage status fields. Frontend uses these to
    # render extraction progress without having to know individual job
    # IDs (the orchestrator creates jobs on the backend after Day 8+12;
    # frontend no longer creates them and so doesn't have their IDs).
    schema_status: str = "pending"
    theory_status: str = "pending"
    questions_status: str = "pending"
    figures_status: str = "pending"
    # ORCH Day 1 + 7 — orchestrator state surfaced so the frontend can
    # distinguish "theory done" vs "theory done AND tail flushed", and
    # display retry counters if surfacing them in the UI later.
    theory_finalized_at: datetime | None = None
    theory_retries: int = 0
    questions_retries: int = 0
    figures_retries: int = 0
    # SCHEMA Rebalance — surface validator warnings to the frontend so
    # the user can see why a schema landed in `needs_review` state (or
    # why an otherwise-accepted schema still has informational warnings).
    schema_warnings: list[dict[str, Any]] | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_orm_book(cls, book) -> "BookOut":
        return cls(
            id=book.id,
            folder_id=book.folder_id,
            title=book.title,
            subject=book.subject,
            pdf_url=book.pdf_url,
            schema_=book.schema,
            analyser=book.analyser,
            status=book.status,
            schema_status=book.schema_status,
            theory_status=book.theory_status,
            questions_status=book.questions_status,
            figures_status=book.figures_status,
            theory_finalized_at=book.theory_finalized_at,
            theory_retries=book.theory_retries,
            questions_retries=book.questions_retries,
            figures_retries=book.figures_retries,
            schema_warnings=book.schema_warnings,
            created_at=book.created_at,
            updated_at=book.updated_at,
        )


class BookUploadResponse(BaseModel):
    book_id: UUID
    job_id: UUID | None = None
    regen_id: UUID | None = None
    status: str
