"""Add section_uuid FK columns to questions and figures.

Phase 1 of canonical identity contract (see CONTRACT.md).

Today's join keys are inconsistent strings:
  questions.section_ref → free String(256), slug or verbatim title
  figures.section_id    → misleadingly named: String(256) slug, NOT a UUID

This migration adds proper UUID FKs alongside the existing string columns.
Nothing is removed. Nothing is renamed. The existing slug-based code keeps
working unchanged. Phase 2 will populate the new columns; Phase 3 will
switch readers to use them; Phase 5 (cleanup) deletes the old columns.

Safe properties:
  * additive only — no drops, no narrowing
  * new columns are nullable — existing rows remain valid
  * ON DELETE SET NULL — no cascading data loss
  * batch_alter_table — works on both SQLite (dev) and Postgres (prod)
  * downgrade restores prior schema exactly

Revision ID: 0022_section_uuid_fks
Revises: 0021_folders
Create Date: 2026-06-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0022_section_uuid_fks"
down_revision: Union[str, None] = "0021_folders"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # questions.section_uuid — nullable FK to sections.id
    with op.batch_alter_table("questions") as batch:
        batch.add_column(
            sa.Column(
                "section_uuid",
                sa.Uuid(as_uuid=True),
                nullable=True,
            )
        )
        batch.create_index("ix_questions_section_uuid", ["section_uuid"])
        batch.create_foreign_key(
            "fk_questions_section_uuid_sections",
            "sections",
            ["section_uuid"],
            ["id"],
            ondelete="SET NULL",
        )

    # figures.section_uuid — nullable FK to sections.id
    # Note: figures.section_id stays as-is (String slug, misleading name).
    # Phase 5 cleanup will rename it to section_slug then drop it.
    with op.batch_alter_table("figures") as batch:
        batch.add_column(
            sa.Column(
                "section_uuid",
                sa.Uuid(as_uuid=True),
                nullable=True,
            )
        )
        batch.create_index("ix_figures_section_uuid", ["section_uuid"])
        batch.create_foreign_key(
            "fk_figures_section_uuid_sections",
            "sections",
            ["section_uuid"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("figures") as batch:
        batch.drop_constraint("fk_figures_section_uuid_sections", type_="foreignkey")
        batch.drop_index("ix_figures_section_uuid")
        batch.drop_column("section_uuid")

    with op.batch_alter_table("questions") as batch:
        batch.drop_constraint("fk_questions_section_uuid_sections", type_="foreignkey")
        batch.drop_index("ix_questions_section_uuid")
        batch.drop_column("section_uuid")
