"""Add page_start/page_end to sections table

Revision ID: 0003_section_page_range
Revises: 0002_user_provider_keys
Create Date: 2026-04-20 00:00:01
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_section_page_range"
down_revision: Union[str, None] = "0002_user_provider_keys"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sections", sa.Column("page_start", sa.Integer(), nullable=True))
    op.add_column("sections", sa.Column("page_end", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("sections", "page_end")
    op.drop_column("sections", "page_start")
