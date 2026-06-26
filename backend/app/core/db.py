"""Async SQLAlchemy engine + session factory."""

from collections.abc import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


engine = create_async_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    # Small bounded pool. There are MANY engines in this app (this async one
    # per uvicorn worker, plus sync engines in heartbeat/watchdog/qa/extract/
    # claude — each created in every uvicorn AND every Celery worker child).
    # Their pools SUM against Postgres's max_connections; oversized pools
    # exhaust it under concurrent load and new connections block → the worker
    # hangs silently. Keep each pool tight + fail-fast instead of blocking.
    pool_size=5,
    max_overflow=5,
    pool_timeout=20,
    pool_recycle=1800,
    future=True,
)


# R6 — Enforce FK cascade for SQLite. Without this, ondelete=CASCADE in the
# ORM models is silently ignored and deleting a question_bank leaves orphan
# question_regenerations / questions pointing at a dead bank_id. We flip
# PRAGMA foreign_keys=ON on every new connection.
if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_fk(dbapi_conn, _):  # pragma: no cover — driver hook
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async session, commits on success, rolls back on error."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
