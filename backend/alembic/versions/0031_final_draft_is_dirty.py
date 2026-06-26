"""Add is_dirty to final_drafts

Tracks whether the user has made manual Composer edits since the last seed.
While dirty, GET /final-draft skips the auto-reseed so edits are preserved
(and reflect in Preview) until an explicit reseed / merge-regen.

Revision ID: 0031_final_draft_is_dirty
Revises: 0030_figure_body_type
Create Date: 2026-06-18 00:00:01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0031_final_draft_is_dirty"
down_revision: Union[str, None] = "0030_figure_body_type"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default must be the SQL literal "false", not "0". SQLite is
    # permissive (reads "0" as Boolean false), but PostgreSQL is strict —
    # `boolean DEFAULT 0` raises DatatypeMismatch on the prod deploy.
    # sa.text("false") works for both dialects.
    op.add_column(
        "final_drafts",
        sa.Column(
            "is_dirty",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("final_drafts", "is_dirty")
