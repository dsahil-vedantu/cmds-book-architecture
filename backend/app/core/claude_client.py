"""Anthropic SDK wrapper with retry + sensible defaults."""

from __future__ import annotations

import logging
from typing import Any

from anthropic import APIStatusError, AsyncAnthropic, RateLimitError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings

logger = logging.getLogger(__name__)

_client: AsyncAnthropic | None = None
_client_key: str | None = None  # track which key the cached client was built with
_keylookup_engine = None  # cached engine for the key lookup (created once)


def _resolve_api_key() -> str | None:
    """Resolve an Anthropic API key.

    Priority:
      1. A user-saved key in ``user_provider_keys`` (provider="anthropic")
      2. The ``ANTHROPIC_API_KEY`` environment setting

    Looked up synchronously so both async (FastAPI) and sync (threaded/Celery)
    callers can reuse this path.
    """
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from app.core.auth import DEV_USER_ID
    from app.core.crypto import decrypt
    from app.models.user_provider_key import UserProviderKey

    try:
        # Cache the engine module-level — the old code built a NEW engine on
        # every call (and never disposed it), leaking a connection pool per
        # Claude-key lookup.
        global _keylookup_engine
        if _keylookup_engine is None:
            _keylookup_engine = create_engine(
                settings.SYNC_DATABASE_URL, pool_pre_ping=True,
                pool_size=1, max_overflow=2, pool_timeout=10, pool_recycle=900,
            )
        with Session(_keylookup_engine) as session:
            row = session.execute(
                select(UserProviderKey).where(
                    UserProviderKey.user_id == DEV_USER_ID,
                    UserProviderKey.provider == "anthropic",
                )
            ).scalar_one_or_none()
            if row is not None:
                import json as _json

                keys = _json.loads(decrypt(row.encrypted_keys))
                user_key = keys.get("api_key")
                if user_key:
                    return str(user_key)
    except Exception as e:  # pragma: no cover — degrade gracefully
        logger.warning("Couldn't load user Anthropic key from DB: %s", e)

    return settings.ANTHROPIC_API_KEY or None


def has_real_key() -> bool:
    """True if a real Anthropic key is configured (via user settings or env)."""
    return bool(_resolve_api_key())


def get_claude() -> AsyncAnthropic:
    """Lazy singleton — rebuilt automatically if the configured API key changes.

    You can set the key either by pasting it in Settings → Providers →
    Anthropic, or via ``ANTHROPIC_API_KEY`` in ``.env``.
    """
    global _client, _client_key
    key = _resolve_api_key()
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not configured. "
            "Paste it in Settings → Providers → Anthropic, or set "
            "ANTHROPIC_API_KEY in .env and restart the backend."
        )
    if _client is None or _client_key != key:
        _client = AsyncAnthropic(api_key=key)
        _client_key = key
    return _client


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=20),
    retry=retry_if_exception_type((RateLimitError, APIStatusError)),
    reraise=True,
)
async def messages_create(
    *,
    model: str | None = None,
    max_tokens: int,
    system: str,
    messages: list[dict[str, Any]],
    **kwargs: Any,
) -> Any:
    """Thin wrapper around client.messages.create with retry on rate limits / 5xx.

    When ``ANTHROPIC_MODE`` resolves to ``mock`` (the default while no API key
    is set), responses come from ``app.core.claude_mock`` instead of the live
    API — the rest of the stack (QC, chunking, persistence) is unchanged.
    """
    mode = settings.anthropic_effective_mode

    if mode == "agent":
        from app.core.claude_agent import messages_create as agent_create

        try:
            return await agent_create(
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                model=model,
                **kwargs,
            )
        except Exception as e:
            logger.warning(
                "Agent SDK failed (%s), falling back to mock responses", e
            )
            from app.core.claude_mock import mock_messages_create

            return await mock_messages_create(
                system=system, messages=messages, max_tokens=max_tokens, **kwargs,
            )

    if mode == "mock":
        from app.core.claude_mock import mock_messages_create

        return await mock_messages_create(
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            **kwargs,
        )

    client = get_claude()
    return await client.messages.create(
        model=model or settings.ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
        **kwargs,
    )


def extract_text(response: Any) -> str:
    """Concatenate text blocks from a Messages API response."""
    parts: list[str] = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "".join(parts)
