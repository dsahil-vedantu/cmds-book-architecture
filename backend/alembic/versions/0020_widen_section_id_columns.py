"""Widen slug-holding columns to VARCHAR(256).

Schema section IDs are slug-style (e.g.
``coordinate-geometry-finding-the-distance-between-two-points-example-8-38``)
which can easily exceed 64 chars on textbooks with many worked-example
subsections. SQLite ignored the column width but Postgres enforces it,
producing ``StringDataRightTruncation`` mid-extract.

Widen the following columns from VARCHAR(64) to VARCHAR(256):

  sections.section_id
  figures.section_id
  figure_regenerations.section_id
  questions.section_ref
  questions.chapter_ref
  rejected_questions.section_ref

Conservative: leaves index, FK, and unique-constraint references intact —
ALTER COLUMN TYPE on a Postgres VARCHAR is non-blocking and idempotent.

Revision ID: 0020_widen_section_id_columns
Revises: 0019_final_drafts
Create Date: 2026-05-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020_widen_section_id_columns"
down_revision: Union[str, None] = "0019_final_drafts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_COLUMNS = [
    ("sections", "section_id", False),
    ("figures", "section_id", False),
    ("figure_regenerations", "section_id", False),
    ("questions", "section_ref", True),     # nullable when question has no section
    ("questions", "chapter_ref", True),
    ("rejected_questions", "section_ref", True),
]


def upgrade() -> None:
    # On SQLite, `alter_column` for column width is a no-op (TEXT is
    # unbounded). On Postgres it actually widens VARCHAR(64) → VARCHAR(256).
    # batch_alter_table handles both dialects cleanly.
    for table, col, nullable in _COLUMNS:
        with op.batch_alter_table(table) as batch:
            batch.alter_column(
                col,
                existing_type=sa.String(64),
                type_=sa.String(256),
                existing_nullable=nullable,
            )


def downgrade() -> None:
    # Don't actually narrow on downgrade — could fail if data already
    # exceeds 64 chars. Just no-op.
    pass
