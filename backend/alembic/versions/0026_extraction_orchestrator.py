"""Add extraction_lock_at + theory_finalized_at to books.

Part of post-schema orchestrator (architecture-v2). These two fields
power the backend-driven extraction coordinator:

  * extraction_lock_at:    When set, the orchestrator is mid-coordination
                           for this book. Prevents duplicate dispatches.
                           Cleared when extraction lifecycle completes or
                           force-released by watchdog after 10 min.

  * theory_finalized_at:   Set AFTER theory + example_linker +
                           figure_embedder all complete. This is the
                           TRUE "theory done" marker. theory_status="done"
                           today is set BEFORE linker/embedder, causing
                           the embedder-double-run race condition.
                           Coordinator gates questions+figures on
                           theory_finalized_at, not theory_status.

Both columns nullable. Existing books unaffected; orchestrator only
acts on new uploads.

Revision ID: 0026_extraction_orchestrator
Revises: 0025_schema_warnings
Create Date: 2026-06-07
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0026_extraction_orchestrator"
down_revision: Union[str, None] = "0025_schema_warnings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("books") as batch:
        batch.add_column(sa.Column(
            "extraction_lock_at", sa.DateTime(timezone=True), nullable=True,
        ))
        batch.add_column(sa.Column(
            "theory_finalized_at", sa.DateTime(timezone=True), nullable=True,
        ))


def downgrade() -> None:
    with op.batch_alter_table("books") as batch:
        batch.drop_column("theory_finalized_at")
        batch.drop_column("extraction_lock_at")
