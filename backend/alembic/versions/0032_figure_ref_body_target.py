"""Add body_target to figure_references.

Companion to 0030 (figures.body_type). The embedder writes the resolved
body slot ("question" vs "solution") onto each figure_reference so the
frontend can render the figure under the question stem vs inside the
solution block without inferring from placeholder text or char offsets.

Was added to the ORM model (app/models/figure_reference.py) without a
matching Alembic revision — present in dev DBs via create_all() but
missing in prod, where boot-time `alembic upgrade head` is the only
schema source. Production deploy on 2026-06-22 failed reading from
figure_references because of this drift. This migration closes the gap.

  body_target: str | None (16 chars)
      "question" → figure renders under Question.raw_text (stem)
      "solution" → figure renders inside Question.solution_text
      NULL       → theory context (not applicable) OR legacy data

  Existing rows: NULL. Intentional — theory figs stay null; legacy
  question figs surface via fallback inference until re-embedding.

Revision ID: 0032_figure_ref_body_target
Revises: 0031_final_draft_is_dirty
Create Date: 2026-06-22
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0032_figure_ref_body_target"
down_revision: Union[str, None] = "0031_final_draft_is_dirty"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("figure_references") as batch:
        batch.add_column(
            sa.Column("body_target", sa.String(16), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("figure_references") as batch:
        batch.drop_column("body_target")
