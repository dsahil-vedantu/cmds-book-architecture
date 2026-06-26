from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Book(Base):
    __tablename__ = "books"

    id: Mapped[UUID] = mapped_column(sa.Uuid(as_uuid=True), primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str | None] = mapped_column(Text)
    # Optional FK into the new ``folders`` table. Nullable so legacy uploads
    # that predate the V-Studio folder concept keep working; the 0021
    # migration backfills all existing rows into a default folder.
    folder_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        ForeignKey("folders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    pdf_url: Mapped[str | None] = mapped_column(Text)  # storage key (S3 or local)
    schema: Mapped[dict | None] = mapped_column(sa.JSON)
    analyser: Mapped[dict | None] = mapped_column(sa.JSON)
    raw_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    # Phase 5 (CONTRACT.md §2 — state contract). Per-stage status fields.
    # Today book.status is unconditionally "ready" regardless of per-stage
    # outcome (extract.py:578). These four fields record the actual outcome
    # of each stage. Eventually book.status becomes derived from these
    # (see derive_book_status()); for now they're populated alongside.
    schema_status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False, server_default="pending",
    )
    theory_status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False, server_default="pending",
    )
    questions_status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False, server_default="pending",
    )
    figures_status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False, server_default="pending",
    )
    # Phase 6 (architecture-v2 — post-schema orchestrator).
    # extraction_lock_at: held by the coordinate_extraction Celery task
    # while it's stepping a book's state machine. Prevents duplicate
    # dispatches. Watchdog force-releases stale locks after 10 minutes
    # of inactivity. NULL when no extraction lifecycle is in progress.
    extraction_lock_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    # theory_finalized_at: set AFTER theory extraction PLUS its tail
    # work (example_linker + figure_embedder) all complete. The
    # coordinator gates questions+figures dispatch on THIS field, not
    # on theory_status (which today is set mid-task before linker
    # runs — causing the embedder-double-run race condition). NULL
    # until theory's full tail has flushed.
    theory_finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    # ORCH Day 7 — per-stage auto-retry counters. The coordinator allows
    # ONE automatic retry on stage failure (transient Gemini error /
    # network blip). After retry=1, the next failure finalizes the book
    # as failed/partial. Manual retry API endpoints (Day 10) reset the
    # counter to 0 so a user-initiated retry can again use the auto-retry
    # slot.
    theory_retries: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default="0",
    )
    # Build Step 1 — schema auto-retry counter. Mirrors the theory/
    # questions/figures counters: the coordinator allows ONE automatic
    # retry on schema failure (transient Gemini error), then leaves the
    # book terminal-failed for the user to retry manually.
    schema_retries: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default="0",
    )
    # Build Step 1 — reconciliation safety net. The watchdog reconciler
    # increments this each time it re-drives a stalled book. When it hits
    # MAX_RECOVERY_ATTEMPTS the book is marked failed and no longer
    # re-driven (anti-infinite-loop). Reset to 0 whenever a stage makes
    # genuine forward progress (pending/failed → running) so a book that
    # recovered cleanly isn't wrongly capped on a later legitimate nudge.
    recovery_attempts: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default="0",
    )
    questions_retries: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default="0",
    )
    figures_retries: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default="0",
    )
    # Last verify_book() report — populated by the quality endpoint.
    # Shape documented in app/services/verify_book.py.
    verification_log: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)

    # SCHEMA Week 1 — observability for schema generation issues.
    # schema_warnings: list of structured warnings the schema generator
    # hit (sanitizer drops, validator violations, postpass-added sections,
    # corrective-retry triggers). Populated by schema_builder during
    # generation. Shape: [{type, section_id, reason, severity}, ...]
    schema_warnings: Mapped[list | None] = mapped_column(sa.JSON, nullable=True)
    # SCHEMA Rebalance — preserve the LAST failed Gemini attempt's schema
    # for offline diagnosis. Set whenever an attempt fails validation
    # (including the final attempt when the loop exhausts MAX_ATTEMPTS and
    # we accept-with-warnings). NULL on books whose first attempt validated
    # cleanly.
    last_failed_schema: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    # schema_quality_score: 0-100. Computed by the schema validator
    # (lands in Week 2). 90+ good, 70-89 has warnings, <70 schema
    # is rejected outright and surfaced to the user.
    schema_quality_score: Mapped[int | None] = mapped_column(
        sa.Integer, nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    sections = relationship(
        "Section",
        back_populates="book",
        cascade="all, delete-orphan",
    )
    regenerations = relationship(
        "Regeneration",
        back_populates="book",
        cascade="all, delete-orphan",
    )
    figures = relationship(
        "Figure",
        back_populates="book",
        cascade="all, delete-orphan",
    )
    figure_regenerations = relationship(
        "FigureRegeneration",
        back_populates="book",
        cascade="all, delete-orphan",
    )
