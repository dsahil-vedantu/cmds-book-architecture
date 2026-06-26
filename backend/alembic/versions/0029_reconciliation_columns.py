"""Add schema_retries + recovery_attempts to books.

Build Step 1 — Reconciliation Orchestration.

  schema_retries:    int, default 0
      Mirrors theory/questions/figures_retries. The coordinator allows ONE
      automatic retry on schema (analyse) failure before leaving the book
      terminal-failed for manual retry.

  recovery_attempts: int, default 0
      The watchdog reconciler increments this each time it re-drives a
      stalled book. Capped at MAX_RECOVERY_ATTEMPTS (5) — past that the
      book is marked failed and no longer re-driven (anti-infinite-loop).
      Reset to 0 on genuine forward progress (a stage advancing
      pending/failed → running) so a recovered book isn't wrongly capped.

Revision ID: 0029_reconciliation_columns
Revises: 0028_schema_rebalance_columns
Create Date: 2026-06-11
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0029_reconciliation_columns"
down_revision: Union[str, None] = "0028_schema_rebalance_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("books") as batch:
        batch.add_column(sa.Column(
            "schema_retries", sa.Integer, nullable=False,
            server_default="0",
        ))
        batch.add_column(sa.Column(
            "recovery_attempts", sa.Integer, nullable=False,
            server_default="0",
        ))


def downgrade() -> None:
    with op.batch_alter_table("books") as batch:
        batch.drop_column("recovery_attempts")
        batch.drop_column("schema_retries")
