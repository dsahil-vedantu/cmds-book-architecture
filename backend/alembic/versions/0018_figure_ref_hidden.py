"""Add is_hidden flag to figure_references for user-controlled removal.

When the user clicks ✕ on an inline/appended figure in the Theory or
Questions tab, we flip the corresponding figure_reference row's
is_hidden=true. Hidden refs are excluded from API responses (theory
embedded_figures / question embedded_figures) and from final-merge
exports.

The Figure row itself stays untouched — the image bytes are still
available; only its placement at THIS spot is suppressed. The user
can unhide later.

Revision ID: 0018_figure_ref_hidden
Revises: 0017_figure_ref_placement
Create Date: 2026-05-18
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0018_figure_ref_hidden"
down_revision: Union[str, None] = "0017_figure_ref_placement"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "figure_references",
        sa.Column(
            "is_hidden",
            sa.Boolean(),
            nullable=False,
            # sa.false() renders correctly per dialect (SQLite 0, Postgres false).
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("figure_references", "is_hidden")
