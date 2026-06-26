"""Add figures and figure_regenerations tables

Revision ID: 0004_figures_and_figure_regenerations
Revises: 0003_section_page_range
Create Date: 2026-04-21 00:00:01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_figures_and_figure_regenerations"
down_revision: Union[str, None] = "0003_section_page_range"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "figures",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("book_id", sa.Uuid(as_uuid=True), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section_id", sa.String(64), nullable=False),
        sa.Column("figure_number", sa.Text(), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("bounding_box", sa.JSON(), nullable=True),
        sa.Column("semantic_type", sa.String(32), nullable=False, server_default="other"),
        sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(32), nullable=False, server_default="extracted"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_figures_book_id", "figures", ["book_id"])
    op.create_index("ix_figures_section_id", "figures", ["section_id"])

    op.create_table(
        "figure_regenerations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("book_id", sa.Uuid(as_uuid=True), sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("figure_id", sa.Uuid(as_uuid=True), sa.ForeignKey("figures.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section_id", sa.String(64), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("style_params", sa.JSON(), nullable=True),
        sa.Column("model_used", sa.String(128), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="completed"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_figure_regenerations_book_id", "figure_regenerations", ["book_id"])
    op.create_index("ix_figure_regenerations_figure_id", "figure_regenerations", ["figure_id"])


def downgrade() -> None:
    op.drop_table("figure_regenerations")
    op.drop_table("figures")
