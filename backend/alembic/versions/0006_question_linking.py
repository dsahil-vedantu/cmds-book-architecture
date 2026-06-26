"""Add linking fields to questions + question_banks for Phase 1 foundation

Revision ID: 0006_question_linking
Revises: 0005_question_banks_and_questions
Create Date: 2026-04-24 00:00:01

Additive only. No theory-service tables are modified.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_question_linking"
down_revision: Union[str, None] = "0005_question_banks_and_questions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Relax section_ref to NULL + add linking columns on questions
    with op.batch_alter_table("questions") as batch:
        batch.alter_column(
            "section_ref",
            existing_type=sa.String(64),
            nullable=True,
        )
        batch.add_column(sa.Column("excluded_block_ref", sa.String(255), nullable=False, server_default=""))
        batch.add_column(sa.Column("excluded_block_index", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("excluded_block_page_start", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("excluded_block_page_end", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("link_method", sa.String(32), nullable=True))
        batch.add_column(sa.Column("link_confidence", sa.Float(), nullable=True))
        batch.add_column(sa.Column("link_rule_trace", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("linked_by", sa.String(16), nullable=False, server_default="system"))
        batch.add_column(sa.Column("linked_at", sa.DateTime(timezone=True), nullable=True))

    op.create_index(
        "ix_questions_excluded_block_ref", "questions", ["excluded_block_ref"]
    )

    # Extend question_banks with snapshot + linking stats
    with op.batch_alter_table("question_banks") as batch:
        batch.add_column(sa.Column("schema_snapshot", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("linking_stats", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("question_banks") as batch:
        batch.drop_column("linking_stats")
        batch.drop_column("schema_snapshot")

    op.drop_index("ix_questions_excluded_block_ref", table_name="questions")
    with op.batch_alter_table("questions") as batch:
        batch.drop_column("linked_at")
        batch.drop_column("linked_by")
        batch.drop_column("link_rule_trace")
        batch.drop_column("link_confidence")
        batch.drop_column("link_method")
        batch.drop_column("excluded_block_page_end")
        batch.drop_column("excluded_block_page_start")
        batch.drop_column("excluded_block_index")
        batch.drop_column("excluded_block_ref")
        batch.alter_column(
            "section_ref",
            existing_type=sa.String(64),
            nullable=False,
        )
