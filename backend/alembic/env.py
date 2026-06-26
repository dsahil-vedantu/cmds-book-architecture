"""Alembic env — uses sync DATABASE_URL from settings and imports all models."""

from logging.config import fileConfig

from alembic import context
import sqlalchemy as sa
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.core.db import Base

# Import all models so they register with Base.metadata
import app.models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.SYNC_DATABASE_URL)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # Postgres: pre-create alembic_version with VARCHAR(128) so alembic
        # doesn't create it with its default VARCHAR(32) (which truncates
        # our longer revision names like "0004_figures_and_figure_regenerations").
        # Idempotent — uses IF NOT EXISTS, and widens an existing narrow column.
        if connection.dialect.name == "postgresql":
            try:
                connection.execute(sa.text(
                    "CREATE TABLE IF NOT EXISTS alembic_version ("
                    "  version_num VARCHAR(128) NOT NULL,"
                    "  CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)"
                    ")"
                ))
                # If the table already existed with the narrow default,
                # widen it. No-op if already VARCHAR(128).
                connection.execute(sa.text(
                    "ALTER TABLE alembic_version "
                    "ALTER COLUMN version_num TYPE VARCHAR(128)"
                ))
                connection.commit()
            except Exception:
                connection.rollback()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
