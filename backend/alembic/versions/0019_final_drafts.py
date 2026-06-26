"""Phase 3.2 — final_drafts table for the authored chapter composition.

One active draft per book. Stores the user's edits (reorder / remove / add /
custom text) as a JSON array of items, so the source extraction tables stay
untouched. Auto-seeded from the current Final Merge state on first open.

Status transitions:
    draft -> exporting -> exported
    draft -> failed (export pipeline error)

Revision ID: 0019_final_drafts
Revises: 0018_figure_ref_hidden
Create Date: 2026-05-19
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0019_final_drafts"
down_revision: Union[str, None] = "0018_figure_ref_hidden"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "final_drafts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "book_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("books.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("items", sa.JSON, nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'draft'"),
        ),
        sa.Column(
            "prefer_regen",
            sa.Boolean(),
            nullable=False,
            # sa.true() renders correctly per dialect (SQLite 1, Postgres true).
            server_default=sa.true(),
        ),
        sa.Column("last_seeded_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )


def downgrade() -> None:
    op.drop_table("final_drafts")
