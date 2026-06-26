"""Add body_type to figures.

F3 — foundation for question-figure routing (F1 / F2).

  body_type: str | None (16 chars)
      Only meaningful when context_hint = "question":
        "question" → figure appears in the question stem (the prompt body)
        "solution" → figure appears in the worked solution
        NULL        → not applicable (theory figs leave this null)

      The embedder uses this to decide whether to embed a labelled
      question figure into Question.raw_text vs Question.solution_text.
      Theory figs ignore the column entirely (kept null).

      Existing rows: NULL. Backfill is intentional — theory figs SHOULD
      stay null; existing question figs are addressed by F1/F2 as a
      separate work item or by re-extraction.

Revision ID: 0030_figure_body_type
Revises: 0029_reconciliation_columns
Create Date: 2026-06-11
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0030_figure_body_type"
down_revision: Union[str, None] = "0029_reconciliation_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("figures") as batch:
        batch.add_column(sa.Column(
            "body_type", sa.String(16), nullable=True,
        ))


def downgrade() -> None:
    with op.batch_alter_table("figures") as batch:
        batch.drop_column("body_type")
