"""Human review for extracted questions.

Revision ID: 0012_question_review
Revises: 0011_job_heartbeat
Create Date: 2026-05-05

Adds:
  • ``questions.is_hidden`` — soft-hide a question via the UI without
    deleting it, so the user can restore later. Default false.
  • ``rejected_questions`` — stores filter-rejected items (or items
    Gemini emitted that the structural filter dropped) so the UI can
    "Restore" them with one click. Each row carries the original
    extracted payload, the reject reason, and the section context.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_question_review"
down_revision: Union[str, None] = "0011_job_heartbeat"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Soft-hide column — additive, default false
    with op.batch_alter_table("questions") as batch:
        batch.add_column(
            sa.Column(
                "is_hidden",
                sa.Boolean(),
                # Use sa.false() so the literal renders correctly per dialect:
                # SQLite → 0, Postgres → false. sa.text("0") fails on Postgres
                # (DatatypeMismatch).
                server_default=sa.false(),
                nullable=False,
            )
        )

    # Rejected items — survive across re-extractions until user restores or
    # explicitly discards. Restoring promotes them into ``questions``.
    op.create_table(
        "rejected_questions",
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
        sa.Column("section_ref", sa.String(64), nullable=True),
        sa.Column("section_title", sa.Text(), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("reject_reason", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decided_by", sa.String(16), nullable=True),
    )
    op.create_index("ix_rejected_questions_bank_id", "rejected_questions", ["bank_id"])
    op.create_index("ix_rejected_questions_section_ref", "rejected_questions", ["section_ref"])


def downgrade() -> None:
    op.drop_index("ix_rejected_questions_section_ref", "rejected_questions")
    op.drop_index("ix_rejected_questions_bank_id", "rejected_questions")
    op.drop_table("rejected_questions")
    with op.batch_alter_table("questions") as batch:
        batch.drop_column("is_hidden")
