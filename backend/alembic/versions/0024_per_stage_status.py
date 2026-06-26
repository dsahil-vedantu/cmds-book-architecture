"""Add per-stage status columns + verification_log to books.

Phase 5 of v2 architecture — state contract (CONTRACT.md §2).

Today `book.status` is a single field set unconditionally to "ready"
at the end of analyse_book regardless of actual outcome (extract.py:578).
This adds four explicit per-stage status fields:

  * schema_status     pending → running → done | failed
  * theory_status     pending → running → done | failed | partial
  * questions_status  pending → running → done | failed | partial
  * figures_status    pending → running → done | failed | partial

Plus a `verification_log` JSON column to record what verify_book()
found (expected vs actual counts, list of empty sections, etc.)

This migration is purely additive. Writers will be updated in a
follow-up to populate the new columns alongside the existing
book.status. Eventually book.status becomes derived from these.

Revision ID: 0024_per_stage_status
Revises: 0023_figure_refs_section_uuid
Create Date: 2026-06-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0024_per_stage_status"
down_revision: Union[str, None] = "0023_figure_refs_section_uuid"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("books") as batch:
        batch.add_column(sa.Column(
            "schema_status", sa.String(32),
            nullable=False, server_default="pending",
        ))
        batch.add_column(sa.Column(
            "theory_status", sa.String(32),
            nullable=False, server_default="pending",
        ))
        batch.add_column(sa.Column(
            "questions_status", sa.String(32),
            nullable=False, server_default="pending",
        ))
        batch.add_column(sa.Column(
            "figures_status", sa.String(32),
            nullable=False, server_default="pending",
        ))
        batch.add_column(sa.Column(
            "verification_log", sa.JSON, nullable=True,
        ))


def downgrade() -> None:
    with op.batch_alter_table("books") as batch:
        batch.drop_column("verification_log")
        batch.drop_column("figures_status")
        batch.drop_column("questions_status")
        batch.drop_column("theory_status")
        batch.drop_column("schema_status")
