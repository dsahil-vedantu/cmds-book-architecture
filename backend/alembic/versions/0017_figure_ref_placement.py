"""Add placement metadata to figure_references — Phase 1 figure embedder.

Records WHERE a figure should be rendered inline within its target
section's theory body or its target question's raw_text. Populated
by the deterministic post-extraction figure_embedder service, NOT
by Gemini.

Columns added:
  - placement_kind   : "inline" | "appended" | "needs_review" | null
                       inline       → figure has a confident label match in
                                      the target text, render at the match
                       appended     → no label match found, append at end
                       needs_review → no match AND user should verify (UI hint)
                       null         → embedder hasn't run yet for this row
  - placement_block_idx  : for theory — which block index in
                           sections.blocks the figure goes AFTER. null
                           when appended or no theory target.
  - placement_char_offset : for question — character offset in
                            question.raw_text where the figure marker
                            should be inserted. null when appended.

Purely additive. No frozen-pipeline columns changed. No data migration
needed — existing rows simply have nulls and the embedder will fill
them when it next runs.

Revision ID: 0017_figure_ref_placement
Revises: 0016_figure_approval
Create Date: 2026-05-18
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0017_figure_ref_placement"
down_revision: Union[str, None] = "0016_figure_approval"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "figure_references",
        sa.Column("placement_kind", sa.String(32), nullable=True),
    )
    op.add_column(
        "figure_references",
        sa.Column("placement_block_idx", sa.Integer, nullable=True),
    )
    op.add_column(
        "figure_references",
        sa.Column("placement_char_offset", sa.Integer, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("figure_references", "placement_char_offset")
    op.drop_column("figure_references", "placement_block_idx")
    op.drop_column("figure_references", "placement_kind")
