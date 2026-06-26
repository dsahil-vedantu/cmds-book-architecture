"""Folders table + books.folder_id + backfill existing books into a default folder.

V-Studio surfaces a "Book Folder" concept above the existing Book table:
folders are user-named containers, and each book row becomes one "chapter"
inside a folder.

This migration is purely additive:

  * creates ``folders`` table
  * adds nullable ``books.folder_id`` FK column
  * inserts one default folder named "Testing"
  * backfills every existing book row's folder_id to point at that folder

No existing column is dropped, renamed, or narrowed. No pipeline, prompt,
or extraction code is touched.

Revision ID: 0021_folders
Revises: 0020_widen_section_id_columns
Create Date: 2026-05-27
"""

from __future__ import annotations

from typing import Sequence, Union
import uuid

import sqlalchemy as sa
from alembic import op


revision: str = "0021_folders"
down_revision: Union[str, None] = "0020_widen_section_id_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Stable UUID for the default folder so re-running the upgrade (in dev) is
# idempotent and the frontend can rely on a known id if needed.
DEFAULT_FOLDER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


def upgrade() -> None:
    # 1. Create folders table.
    op.create_table(
        "folders",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("color", sa.String(9), nullable=False, server_default="#1A237E"),
        sa.Column("subject", sa.Text(), nullable=True),
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
    )

    # 2. Add nullable folder_id to books, with FK + index.
    with op.batch_alter_table("books") as batch:
        batch.add_column(
            sa.Column(
                "folder_id",
                sa.Uuid(as_uuid=True),
                nullable=True,
            )
        )
        batch.create_index("ix_books_folder_id", ["folder_id"])
        batch.create_foreign_key(
            "fk_books_folder_id_folders",
            "folders",
            ["folder_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # 3. Insert default folder.
    #
    # Storage format note: SQLAlchemy's ``sa.Uuid(as_uuid=True)`` stores
    # UUIDs differently per dialect — SQLite uses 32-char hex (no hyphens),
    # Postgres uses native UUID. To stay compatible across both, we pass
    # the UUID via SQLAlchemy's binding layer (which knows the per-dialect
    # canonical form), not as a raw string.
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    default_id_db: str = DEFAULT_FOLDER_ID.hex if is_sqlite else str(DEFAULT_FOLDER_ID)
    bind.execute(
        sa.text(
            "INSERT INTO folders (id, name, color, subject) "
            "VALUES (:id, :name, :color, :subject)"
        ),
        {
            "id": default_id_db,
            "name": "Testing",
            "color": "#1A237E",
            "subject": None,
        },
    )

    # 4. Backfill every existing book into the default folder.
    bind.execute(
        sa.text("UPDATE books SET folder_id = :fid WHERE folder_id IS NULL"),
        {"fid": default_id_db},
    )


def downgrade() -> None:
    with op.batch_alter_table("books") as batch:
        batch.drop_constraint("fk_books_folder_id_folders", type_="foreignkey")
        batch.drop_index("ix_books_folder_id")
        batch.drop_column("folder_id")
    op.drop_table("folders")
