"""Question regeneration scaffolding.

Revision ID: 0010_question_regenerations
Revises: 0009_question_qc
Create Date: 2026-04-28

Adds:
* ``question_regenerations`` — one row per regen run (bank-scoped).
* ``questions.regen_id`` — nullable FK; original rows have NULL.
* ``questions.source_regen_id`` — nullable FK; the parent regen this run was
  produced from (NULL when the parent is the original extraction).

Additive-only. Existing rows keep ``regen_id IS NULL`` and remain the canonical
"original" tree.
"""

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0010_question_regenerations"
down_revision: Union[str, None] = "0009_question_qc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "question_regenerations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, default=uuid4),
        sa.Column(
            "bank_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("question_banks.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "book_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("books.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "source_regen_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("question_regenerations.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("label", sa.String(64), nullable=True),
        sa.Column("scope", sa.String(16), nullable=False, server_default="bank"),
        sa.Column("section_refs", sa.JSON, nullable=True),
        sa.Column("custom_instructions", sa.Text, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=True, index=True),
        sa.Column("extraction_stats", sa.JSON, nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )

    with op.batch_alter_table("questions") as batch:
        batch.add_column(sa.Column("regen_id", sa.Uuid(as_uuid=True), nullable=True))
        batch.add_column(sa.Column("source_regen_id", sa.Uuid(as_uuid=True), nullable=True))
        batch.create_foreign_key(
            "fk_questions_regen_id",
            "question_regenerations",
            ["regen_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_foreign_key(
            "fk_questions_source_regen_id",
            "question_regenerations",
            ["source_regen_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_questions_regen_id", ["regen_id"])
        batch.create_index("ix_questions_source_regen_id", ["source_regen_id"])


def downgrade() -> None:
    with op.batch_alter_table("questions") as batch:
        batch.drop_index("ix_questions_source_regen_id")
        batch.drop_index("ix_questions_regen_id")
        batch.drop_constraint("fk_questions_source_regen_id", type_="foreignkey")
        batch.drop_constraint("fk_questions_regen_id", type_="foreignkey")
        batch.drop_column("source_regen_id")
        batch.drop_column("regen_id")
    op.drop_table("question_regenerations")
