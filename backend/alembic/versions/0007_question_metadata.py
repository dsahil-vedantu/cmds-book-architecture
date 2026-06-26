"""Add Stage 2 extraction metadata to questions + extraction_stats to question_banks.

Revision ID: 0007_question_metadata
Revises: 0006_question_linking
Create Date: 2026-04-24 00:00:02

Additive only. Captures per-question OCR tags (question_number, exercise_ref,
chapter_ref, sub_part, question_type, has_options, solution_text, has_solution,
identified_total) and per-bank extraction stats + last_error.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_question_metadata"
down_revision: Union[str, None] = "0006_question_linking"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("questions") as batch:
        batch.add_column(sa.Column("question_number", sa.String(32), nullable=True))
        batch.add_column(sa.Column("exercise_ref", sa.String(128), nullable=True))
        batch.add_column(sa.Column("chapter_ref", sa.String(64), nullable=True))
        batch.add_column(sa.Column("sub_part", sa.String(8), nullable=True))
        batch.add_column(sa.Column("question_type", sa.String(32), nullable=True))
        batch.add_column(
            sa.Column("has_options", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("solution_text", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column("has_solution", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("identified_total", sa.Integer(), nullable=True))

    with op.batch_alter_table("question_banks") as batch:
        batch.add_column(sa.Column("extraction_stats", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("last_error", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("question_banks") as batch:
        batch.drop_column("last_error")
        batch.drop_column("extraction_stats")

    with op.batch_alter_table("questions") as batch:
        batch.drop_column("identified_total")
        batch.drop_column("has_solution")
        batch.drop_column("solution_text")
        batch.drop_column("has_options")
        batch.drop_column("question_type")
        batch.drop_column("sub_part")
        batch.drop_column("chapter_ref")
        batch.drop_column("exercise_ref")
        batch.drop_column("question_number")
