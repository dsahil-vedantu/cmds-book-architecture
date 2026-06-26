"""Per-regen parameters for v3 question regeneration.

Revision ID: 0013_question_regen_params
Revises: 0012_question_review
Create Date: 2026-05-11

Adds 4 columns to question_regenerations to persist user-supplied regen params
(R4/R5 of the regen rollout):
  • similarity_level  — one of 5 modes from question_regenerator_v3.txt
  • count             — variants to generate per source question (default 3)
  • question_type     — output format ("same_as_source" or one of 14 types)
  • priority_mode     — how custom_instructions apply: override | layer_on_top
                        | specific_aspects

All columns are nullable; the v3 worker uses sane defaults when unset. The
existing v2 worker ignores these columns entirely (no API/v2 breakage).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_question_regen_params"
down_revision: Union[str, None] = "0012_question_review"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("question_regenerations") as batch:
        batch.add_column(sa.Column("similarity_level", sa.String(64), nullable=True))
        batch.add_column(sa.Column("count", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("question_type", sa.String(64), nullable=True))
        batch.add_column(sa.Column("priority_mode", sa.String(32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("question_regenerations") as batch:
        batch.drop_column("priority_mode")
        batch.drop_column("question_type")
        batch.drop_column("count")
        batch.drop_column("similarity_level")
