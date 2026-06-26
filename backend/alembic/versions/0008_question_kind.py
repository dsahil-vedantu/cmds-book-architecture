"""Add kind column to questions for per-section folder grouping.

Revision ID: 0008_question_kind
Revises: 0007_question_metadata
Create Date: 2026-04-24 00:00:03

Additive-only. Captures the kind of question item as printed on the page —
"exercise" | "example" | "problem" | "try_it" | "review" | "mcq" | "other".
Used to group per-section folders in the sidebar (Examples, Problems, etc).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_question_kind"
down_revision: Union[str, None] = "0007_question_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("questions") as batch:
        batch.add_column(
            sa.Column(
                "kind",
                sa.String(16),
                nullable=False,
                server_default="exercise",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("questions") as batch:
        batch.drop_column("kind")
