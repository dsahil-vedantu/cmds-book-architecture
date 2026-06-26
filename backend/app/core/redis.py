"""Async Redis client — used by rate_limit; degrades open if unreachable.

In local mode (TASK_EXECUTOR=inline), Redis is optional. Rate limiting will
automatically skip when the Redis backend is unavailable.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


class _StubRedis:
    """In-memory stub used when Redis isn't installed / isn't reachable."""

    async def incr(self, _key: str) -> int:
        return 0

    async def expire(self, _key: str, _ttl: int) -> bool:
        return False


def _build_client() -> Any:
    try:
        import redis.asyncio as aioredis  # type: ignore[import-not-found]
    except ImportError:
        logger.info("redis package not installed — rate-limiting disabled in local mode")
        return _StubRedis()
    try:
        return aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
    except Exception as e:
        logger.warning("Redis unavailable (%s) — using stub", e)
        return _StubRedis()


redis_client: Any = _build_client()


async def get_redis() -> Any:
    return redis_client
