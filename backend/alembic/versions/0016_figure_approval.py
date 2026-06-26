"""Add approved_at column to figures — Q5 review workflow.

When the user clicks "✓ Approve & move to Regenerated" after a regen run,
we stamp `approved_at` on each kept Figure row. The ✨ Regenerated sidebar
folder filters to figures with `approved_at IS NOT NULL`.

Status transitions:
  none / draft → regenerating → ready (draft variant exists)
  ready → approved (variant moved to Regenerated folder)
  approved → ready (user clicks Unapprove, drops back to draft)

Purely additive. No frozen-pipeline columns changed.

Revision ID: 0016_figure_approval
Revises: 0015_figures_v2
Create Date: 2026-05-13
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016_figure_approval"
down_revision: Union[str, None] = "0015_figures_v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("figures") as batch:
        batch.add_column(
            sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True)
        )
    op.create_index("ix_figures_approved", "figures", ["book_id", "approved_at"])


def downgrade() -> None:
    op.drop_index("ix_figures_approved", table_name="figures")
    with op.batch_alter_table("figures") as batch:
        batch.drop_column("approved_at")
