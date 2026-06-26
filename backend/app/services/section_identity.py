"""Canonical section identity resolution.

Per CONTRACT.md §1: section identity is a UUID. All joins use UUIDs.
Slugs are display-only.

During the migration (Phase 2/3), writers still receive slug-based
references from upstream code (e.g. `unit.id` is a slug string in
the question worker). This module provides the single canonical way
to resolve a slug → Section.id (UUID) at write time.

Once Phase 4 ships and slugs are no longer used for joins, this module
shrinks to a no-op pass-through. Until then, every write that has a
slug and an FK column should call `resolve_section_uuid()` and store
the result.

Design notes:
- Sync function — callable from both sync workers (questions_v3, figure_embedder)
  and async paths via `run_in_executor` if needed.
- Cache-friendly: callers should batch lookups when persisting many rows
  for the same book.
- Returns None on miss — does NOT raise. The caller writes None into the
  FK column, which is allowed (the column is nullable during migration).
  Phase 4 will tighten this to "raise on miss for new uploads".
"""

from __future__ import annotations

from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.section import Section


def resolve_section_uuid(
    session: Session,
    book_id: UUID | str,
    section_slug: str | None,
) -> UUID | None:
    """Resolve a (book_id, section_slug) pair to the Section's UUID.

    Returns None if no matching section exists. Single-row lookup —
    for bulk operations prefer `build_section_uuid_map()`.
    """
    if not section_slug:
        return None
    row = session.execute(
        select(Section.id).where(
            Section.book_id == book_id,
            Section.section_id == section_slug,
        )
    ).first()
    return row[0] if row else None


def build_section_uuid_map(
    session: Session,
    book_id: UUID | str,
) -> dict[str, UUID]:
    """Build {section_slug: section_uuid} for a whole book.

    Use this when you're about to persist many rows for the same book
    (e.g. question worker writing 50 questions across 10 sections).
    Avoids one DB round-trip per row.
    """
    rows = session.execute(
        select(Section.section_id, Section.id).where(Section.book_id == book_id)
    ).all()
    return {slug: sec_id for slug, sec_id in rows if slug}
