"""Add section_uuid FK to figure_references (companion to 0022).

Phase 2 of canonical identity contract — figure_references is the link
table between Figure and the section where it's placed. It currently
uses `section_ref` (string slug) as its join key. This adds a proper
UUID FK alongside, matching the pattern from 0022.

Additive only — section_ref kept for legacy paths; section_uuid is
nullable. Phase 5 cleanup removes section_ref after Phase 4 flip.

Revision ID: 0023_figure_refs_section_uuid
Revises: 0022_section_uuid_fks
Create Date: 2026-06-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0023_figure_refs_section_uuid"
down_revision: Union[str, None] = "0022_section_uuid_fks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("figure_references") as batch:
        batch.add_column(
            sa.Column(
                "section_uuid",
                sa.Uuid(as_uuid=True),
                nullable=True,
            )
        )
        batch.create_index("ix_figure_refs_section_uuid", ["section_uuid"])
        batch.create_foreign_key(
            "fk_figure_refs_section_uuid_sections",
            "sections",
            ["section_uuid"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("figure_references") as batch:
        batch.drop_constraint(
            "fk_figure_refs_section_uuid_sections", type_="foreignkey"
        )
        batch.drop_index("ix_figure_refs_section_uuid")
        batch.drop_column("section_uuid")
