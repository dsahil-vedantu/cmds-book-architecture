"""Add schema_warnings (JSON) + schema_quality_score (int) to books.

Part of SCHEMA Week 1 — observability foundation.

These two fields surface every issue the schema generator hit:
  * schema_warnings:      list of structured warning dicts (each with
                          type, section_id, reason, severity)
  * schema_quality_score: 0-100 score computed by the schema validator.
                          90+ = good, 70-89 = warnings present,
                          <70 = schema rejected and surfaced to user.

Today (pre-Week-2): sanitizer drops/fixes things silently. With these
fields populated, every drop becomes user-visible via /quality endpoint.
Week 2 validator + Week 3 corrective retries replace silent fixes
with structured warnings here.

Additive migration — both columns nullable, default empty/null. Existing
books are unaffected; new uploads start populating immediately.

Revision ID: 0025_schema_warnings
Revises: 0024_per_stage_status
Create Date: 2026-06-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0025_schema_warnings"
down_revision: Union[str, None] = "0024_per_stage_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("books") as batch:
        batch.add_column(sa.Column(
            "schema_warnings", sa.JSON, nullable=True,
        ))
        batch.add_column(sa.Column(
            "schema_quality_score", sa.Integer, nullable=True,
        ))


def downgrade() -> None:
    with op.batch_alter_table("books") as batch:
        batch.drop_column("schema_quality_score")
        batch.drop_column("schema_warnings")
