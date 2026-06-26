"""Add jobs.last_heartbeat_at for watchdog stale-job detection.

Revision ID: 0011_job_heartbeat
Revises: 0010_question_regenerations
Create Date: 2026-04-28

The watchdog (app.core.watchdog) scans every 60s for jobs whose
``last_heartbeat_at`` is older than the stale threshold and marks them
``failed``. Heartbeats are emitted by the in-flight worker every ~10s.

Additive only — pre-existing rows are NULL and the watchdog tolerates that
(falls back to ``started_at`` for the staleness check).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_job_heartbeat"
down_revision: Union[str, None] = "0010_question_regenerations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(
            sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.create_index("ix_jobs_last_heartbeat_at", ["last_heartbeat_at"])


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_last_heartbeat_at")
        batch.drop_column("last_heartbeat_at")
