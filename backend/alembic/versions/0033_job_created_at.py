"""Add created_at to jobs

Needed by the watchdog's "zombie queued job" sweep — it must be able to
ask "how long has this Job been in `queued` status?" Without created_at,
a freshly-queued Job has neither started_at nor last_heartbeat_at, so
its age can't be measured and the sweep can't fire.

Bug B1 from docs/orchestrator-bugs.md: my commit 98f079e referenced
Job.created_at on the assumption it existed; it didn't, so the entire
watchdog `_scan_once` was crashing every 60s in prod. This adds the
column for real.

Revision ID: 0033_job_created_at
Revises: 0032_figure_ref_body_target
Create Date: 2026-06-22 17:50:00
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0033_job_created_at"
down_revision: Union[str, None] = "0032_figure_ref_body_target"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing rows get CURRENT_TIMESTAMP via the server_default — accurate
    # enough for the watchdog (which only cares about "is this older than
    # 5 min?"; existing rows will simply not look stale on first scan).
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ))
        batch.create_index(
            "ix_jobs_created_at", ["created_at"],
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_created_at")
        batch.drop_column("created_at")
