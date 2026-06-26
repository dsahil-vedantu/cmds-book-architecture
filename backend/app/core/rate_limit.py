"""Simple Redis-backed rate limiter — fixed-window counter per key."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import HTTPException, Request

from app.core.auth import get_current_user_id
from app.core.redis import redis_client

logger = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, *, limit: int, window_seconds: int, bucket: str):
        self.limit = limit
        self.window = window_seconds
        self.bucket = bucket

    async def __call__(self, request: Request) -> None:
        user_id = await get_current_user_id(request.headers.get("x-user-id"))
        key = f"rl:{self.bucket}:{user_id}"
        try:
            count = await redis_client.incr(key)
            if count == 1:
                await redis_client.expire(key, self.window)
        except Exception as e:  # degrade open if Redis is down
            logger.warning("rate-limit backend unavailable: %s", e)
            return
        if count > self.limit:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded: {self.limit}/{self.window}s for {self.bucket}",
            )


# Default: 100 extractions per day per user (per the brief).
extraction_limit = RateLimiter(limit=100, window_seconds=60 * 60 * 24, bucket="extraction")
