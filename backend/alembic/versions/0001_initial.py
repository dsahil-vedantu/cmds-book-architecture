"""initial schema: books, sections, regenerations, jobs (cross-DB)

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-17 00:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "books",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text()),
        sa.Column("pdf_url", sa.Text()),
        sa.Column("schema", sa.JSON()),
        sa.Column("analyser", sa.JSON()),
        sa.Column("raw_text", sa.Text()),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
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
            nullable=False,
        ),
    )

    op.create_table(
        "sections",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "book_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("books.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("section_id", sa.String(64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("level", sa.Integer()),
        sa.Column("bloom", sa.Integer()),
        sa.Column("tags", sa.JSON()),
        sa.Column("blocks", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("source_chunk", sa.Text()),
        sa.Column("qc_local", sa.JSON()),
        sa.Column("qc_llm", sa.JSON()),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("book_id", "section_id", name="uq_sections_book_section"),
    )
    op.create_index("ix_sections_book_id", "sections", ["book_id"])

    op.create_table(
        "regenerations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "book_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("books.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("blocks_by_section", sa.JSON(), nullable=False),
        sa.Column("qc_drift", sa.JSON()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_regenerations_book_id", "regenerations", ["book_id"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "book_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("books.id", ondelete="SET NULL"),
        ),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("message", sa.Text()),
        sa.Column("error", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_jobs_book_id", "jobs", ["book_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_book_id", "jobs")
    op.drop_table("jobs")
    op.drop_index("ix_regenerations_book_id", "regenerations")
    op.drop_table("regenerations")
    op.drop_index("ix_sections_book_id", "sections")
    op.drop_table("sections")
    op.drop_table("books")
