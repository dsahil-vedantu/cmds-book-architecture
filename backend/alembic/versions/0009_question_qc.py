"""QA agent scaffolding — per-question QC columns + question_qa_runs table.

Revision ID: 0009_question_qc
Revises: 0008_question_kind
Create Date: 2026-04-24 00:00:04

Additive-only. Stores deterministic fidelity scoring per question (Pillar B)
and per-section QA run snapshots. LLM-generated test specs (Pillar A) also
live in the same ``qc_tests`` JSON blob — each entry is a test spec + result.
"""

from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "0009_question_qc"
down_revision: Union[str, None] = "0008_question_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Per-question QC fields
    with op.batch_alter_table("questions") as batch:
        batch.add_column(
            sa.Column(
                "qc_status",
                sa.String(16),
                nullable=False,
                server_default="pending",
            )
        )
        batch.add_column(sa.Column("qc_score", sa.Float, nullable=True))
        batch.add_column(sa.Column("qc_tests", sa.JSON, nullable=True))

    # 2. Per-section run snapshots (immutable — one row per run per section)
    op.create_table(
        "question_qa_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, default=uuid4),
        sa.Column(
            "bank_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("question_banks.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("section_ref", sa.String(128), nullable=True, index=True),
        sa.Column("expected_total", sa.Integer, nullable=False, server_default="0"),
        sa.Column("extracted_total", sa.Integer, nullable=False, server_default="0"),
        sa.Column("missed", sa.Integer, nullable=False, server_default="0"),
        sa.Column("hallucinated", sa.Integer, nullable=False, server_default="0"),
        sa.Column("verbatim_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("paraphrased_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("not_verbatim_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("score", sa.Float, nullable=True),
        sa.Column("failures", sa.JSON, nullable=True),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("prompt_version", sa.String(32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("question_qa_runs")
    with op.batch_alter_table("questions") as batch:
        batch.drop_column("qc_tests")
        batch.drop_column("qc_score")
        batch.drop_column("qc_status")
