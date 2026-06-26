"""Add per-stage retry counters to books for auto-retry-once logic.

Part of ORCH Day 7. The coordinator uses these to allow ONE automatic
retry on stage failure (transient Gemini issue, network blip), then
finalizes the book as failed/partial on the second failure.

  theory_retries:    int, default 0
  questions_retries: int, default 0
  figures_retries:   int, default 0

Retry policy:
  status="failed" + retries=0 → coordinator increments + dispatches again
  status="failed" + retries>=1 → coordinator finalizes (no more auto-retry)

Manual retry buttons (Day 10) will reset the counter so a user-initiated
retry can again use the auto-retry slot.

Revision ID: 0027_extraction_retry_counters
Revises: 0026_extraction_orchestrator
Create Date: 2026-06-07
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0027_extraction_retry_counters"
down_revision: Union[str, None] = "0026_extraction_orchestrator"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("books") as batch:
        batch.add_column(sa.Column(
            "theory_retries", sa.Integer, nullable=False,
            server_default="0",
        ))
        batch.add_column(sa.Column(
            "questions_retries", sa.Integer, nullable=False,
            server_default="0",
        ))
        batch.add_column(sa.Column(
            "figures_retries", sa.Integer, nullable=False,
            server_default="0",
        ))


def downgrade() -> None:
    with op.batch_alter_table("books") as batch:
        batch.drop_column("figures_retries")
        batch.drop_column("questions_retries")
        batch.drop_column("theory_retries")
