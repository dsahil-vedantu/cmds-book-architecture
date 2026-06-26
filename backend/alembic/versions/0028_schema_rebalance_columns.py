"""Add books.last_failed_schema column.

books.schema_warnings already exists. This migration adds
last_failed_schema (JSON, nullable) used by the schema-rebalance work
to preserve the most recent failed Gemini attempt for offline diagnosis.

Revision ID: 0028_schema_rebalance_columns
Revises: 0027_extraction_retry_counters
Create Date: 2026-06-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028_schema_rebalance_columns"
down_revision: Union[str, None] = "0027_extraction_retry_counters"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("books") as batch:
        batch.add_column(sa.Column("last_failed_schema", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("books") as batch:
        batch.drop_column("last_failed_schema")
