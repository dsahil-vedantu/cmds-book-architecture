"""Figures pipeline v2 — additive only.

Additions to existing `figures` table (already created by 0004):
  - image_bytes (LargeBinary)            — original cropped image bytes
  - regen_image_bytes (LargeBinary)      — latest regenerated variant bytes
  - mime_type (varchar)                  — defaults to image/png
  - regen_version (Integer)              — increments on each regen, 0=none
  - regen_status (varchar)               — none|extracting|ready|failed
  - figure_id_text (varchar)             — Gemini's stable id like "fig_8_1"
  - normalized_label (varchar)           — composite-key half (e.g. "8.1")
  - source_hash (varchar)                — sha256 of image_bytes (cache key)
  - regen_cache_key (varchar)            — sha256(source_hash + style + params)
  - context (varchar)                    — theory|question|both (denormalized
                                            convenience; canonical link in
                                            figure_references table)

New `figure_references` table — many-to-many between figures and (section,
context, question). Lets one Figure row be referenced by multiple
placeholders (theory + multiple questions).

New `figure_regenerations_v2` — DISTINCT from existing figure_regenerations
(0004) which is per-figure history. v2 is per-(section, regen-run) like the
question regen pattern. Keep both tables; nothing reads from the old one
yet so leaving it intact is harmless.

Revision ID: 0015_figures_v2
Revises: 0014_question_source_link
Create Date: 2026-05-12
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_figures_v2"
down_revision: Union[str, None] = "0014_question_source_link"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Additive columns on existing `figures` table (created by 0004).
    with op.batch_alter_table("figures") as batch:
        batch.add_column(sa.Column("image_bytes", sa.LargeBinary(), nullable=True))
        batch.add_column(sa.Column("regen_image_bytes", sa.LargeBinary(), nullable=True))
        batch.add_column(sa.Column("mime_type", sa.String(64), nullable=True,
                                    server_default="image/png"))
        batch.add_column(sa.Column("regen_version", sa.Integer(), nullable=False,
                                    server_default="0"))
        batch.add_column(sa.Column("regen_status", sa.String(32), nullable=False,
                                    server_default="none"))
        batch.add_column(sa.Column("figure_id_text", sa.String(64), nullable=True))
        batch.add_column(sa.Column("normalized_label", sa.String(64), nullable=True))
        batch.add_column(sa.Column("source_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("regen_cache_key", sa.String(64), nullable=True))
        batch.add_column(sa.Column("context_hint", sa.String(32), nullable=True))
        batch.add_column(sa.Column("regen_meta", sa.JSON(), nullable=True))

    op.create_index(
        "ix_figures_normalized_label",
        "figures",
        ["book_id", "section_id", "normalized_label"],
    )
    op.create_index("ix_figures_source_hash", "figures", ["source_hash"])
    op.create_index("ix_figures_cache_key", "figures", ["regen_cache_key"])

    # figure_references — many placeholders can point to one Figure.
    op.create_table(
        "figure_references",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "figure_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("figures.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "book_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("books.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("section_ref", sa.String(255), nullable=False),
        sa.Column("context", sa.String(32), nullable=False),  # theory|question
        sa.Column(
            "question_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("questions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("placeholder_text", sa.Text(), nullable=True),
        sa.Column("link_method", sa.String(32), nullable=False,
                  server_default="auto"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_figure_references_figure_id", "figure_references", ["figure_id"])
    op.create_index("ix_figure_references_book_id", "figure_references", ["book_id"])
    op.create_index(
        "ix_figure_references_section",
        "figure_references",
        ["book_id", "section_ref"],
    )
    op.create_index(
        "ix_figure_references_question_id",
        "figure_references",
        ["question_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_figure_references_question_id", table_name="figure_references")
    op.drop_index("ix_figure_references_section", table_name="figure_references")
    op.drop_index("ix_figure_references_book_id", table_name="figure_references")
    op.drop_index("ix_figure_references_figure_id", table_name="figure_references")
    op.drop_table("figure_references")

    op.drop_index("ix_figures_cache_key", table_name="figures")
    op.drop_index("ix_figures_source_hash", table_name="figures")
    op.drop_index("ix_figures_normalized_label", table_name="figures")
    with op.batch_alter_table("figures") as batch:
        batch.drop_column("regen_meta")
        batch.drop_column("context_hint")
        batch.drop_column("regen_cache_key")
        batch.drop_column("source_hash")
        batch.drop_column("normalized_label")
        batch.drop_column("figure_id_text")
        batch.drop_column("regen_status")
        batch.drop_column("regen_version")
        batch.drop_column("mime_type")
        batch.drop_column("regen_image_bytes")
        batch.drop_column("image_bytes")
