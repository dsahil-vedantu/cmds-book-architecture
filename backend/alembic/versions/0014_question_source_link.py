"""Source-question link for regen rows.

Revision ID: 0014_question_source_link
Revises: 0013_question_regen_params
Create Date: 2026-05-11

Adds:
  • ``questions.source_question_id`` — FK to questions.id, nullable.
    Set on regen rows (regen_id IS NOT NULL) to point back to the
    original (extracted) Question that was used as the regen source.
    Lets the UI group N variants under their specific source instead
    of mixing them at section level.

Original rows always have ``source_question_id IS NULL``.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_question_source_link"
down_revision: Union[str, None] = "0013_question_regen_params"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("questions") as batch:
        batch.add_column(
            sa.Column(
                "source_question_id",
                sa.Uuid(as_uuid=True),
                nullable=True,
            )
        )
        batch.create_index(
            "ix_questions_source_question_id",
            ["source_question_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("questions") as batch:
        batch.drop_index("ix_questions_source_question_id")
        batch.drop_column("source_question_id")
