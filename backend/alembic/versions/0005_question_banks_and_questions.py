"""Add question_banks and questions tables

Revision ID: 0005_question_banks_and_questions
Revises: 0004_figures_and_figure_regenerations
Create Date: 2026-04-23 00:00:01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_question_banks_and_questions"
down_revision: Union[str, None] = "0004_figures_and_figure_regenerations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "question_banks",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "book_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("books.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_question_banks_book_id", "question_banks", ["book_id"])

    op.create_table(
        "questions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "bank_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("question_banks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "book_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("books.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("section_ref", sa.String(64), nullable=False),
        sa.Column("section_title", sa.Text(), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("qc_local", sa.JSON(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_questions_bank_id", "questions", ["bank_id"])
    op.create_index("ix_questions_book_id", "questions", ["book_id"])
    op.create_index("ix_questions_section_ref", "questions", ["section_ref"])


def downgrade() -> None:
    op.drop_table("questions")
    op.drop_table("question_banks")
