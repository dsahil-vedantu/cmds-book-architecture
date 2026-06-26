"""user_provider_keys table (Sprint 4, cross-DB)

Revision ID: 0002_user_provider_keys
Revises: 0001_initial
Create Date: 2026-04-17 00:00:01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_user_provider_keys"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_provider_keys",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("encrypted_keys", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "provider", name="uq_user_provider"),
    )
    op.create_index("ix_user_provider_keys_user_id", "user_provider_keys", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_provider_keys_user_id", "user_provider_keys")
    op.drop_table("user_provider_keys")
