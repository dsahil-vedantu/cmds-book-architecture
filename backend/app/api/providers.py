"""Providers router — list providers, save user-supplied keys, run test extractions.

Keys are encrypted at rest with Fernet; the plaintext never leaves the
`/keys` endpoint. Key updates are validated with a health_check before they
replace any previously saved value.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, sessionmaker

from app.core.auth import get_current_user_id
from app.core.config import settings as app_settings
from app.core.crypto import decrypt, encrypt
from app.core.db import get_session
from app.models.user_provider_key import UserProviderKey
from app.providers.base import OCRProvider
from app.providers.router import build_registry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/providers", tags=["providers"])

# Dedicated sync engine so list_providers can load per-user encrypted keys
# synchronously (the registry builder is sync-by-design).
_sync_engine_for_providers = create_engine(
    app_settings.SYNC_DATABASE_URL, pool_pre_ping=True
)
_SyncSession = sessionmaker(bind=_sync_engine_for_providers, class_=Session, autoflush=False)


async def _validate_anthropic_key(api_key: str) -> bool:
    """Ping the Anthropic API with the supplied key to confirm it works."""
    if not api_key:
        return False
    try:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=api_key)
        await client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=5,
            messages=[{"role": "user", "content": "ping"}],
        )
        return True
    except Exception as e:
        logger.info("Anthropic key validation failed: %s", e)
        return False


def _instantiate_for_test(provider_name: str, keys: dict[str, Any]) -> OCRProvider:
    """Build a provider instance from the supplied plaintext keys."""
    if provider_name == "anthropic":
        from app.providers.anthropic import AnthropicProvider

        return AnthropicProvider()
    if provider_name == "mathpix":
        from app.providers.mathpix import MathpixProvider

        return MathpixProvider(keys.get("app_id", ""), keys.get("app_key", ""))
    if provider_name == "sarvam":
        from app.providers.sarvam import SarvamProvider

        return SarvamProvider(keys.get("api_key", ""))
    if provider_name == "google_vision":
        from app.providers.google_vision import GoogleVisionProvider

        return GoogleVisionProvider(
            keys.get("credentials_json") or {},
            keys.get("gcs_bucket") or "",
        )
    raise HTTPException(400, detail=f"Unknown provider: {provider_name}")


ALL_PROVIDERS = ["anthropic", "mathpix", "sarvam", "google_vision"]


@router.get("")
async def list_providers(
    user_id: UUID = Depends(get_current_user_id),
) -> list[dict]:
    """List every known provider with a per-user configured / healthy flag.

    Providers the user hasn't configured still appear (so the settings UI can
    show them), but with ``configured=false`` and ``healthy=false``.
    """
    with _SyncSession() as session_sync:
        registry = build_registry(session_sync=session_sync, user_id=user_id)

    # Metadata for providers the user hasn't configured yet
    from app.providers.anthropic import AnthropicProvider

    stubs: dict[str, dict[str, Any]] = {
        "mathpix": {
            "handles": ["equations", "math", "tables"],
            "avg_time_per_page": 2.0,
        },
        "sarvam": {
            "handles": ["hindi", "tamil", "bengali", "telugu", "indic"],
            "avg_time_per_page": 4.0,
        },
        "google_vision": {
            "handles": ["scanned", "image_pdf"],
            "avg_time_per_page": 5.0,
        },
        "anthropic": {
            "handles": AnthropicProvider.handles,
            "avg_time_per_page": AnthropicProvider.avg_time_per_page,
        },
    }

    # "configured" for Anthropic means a real key is resolvable (user-saved or
    # env) — otherwise we're in mock mode and shouldn't pretend otherwise.
    from app.core.claude_client import has_real_key as _anthropic_has_key

    out: list[dict] = []
    for name in ALL_PROVIDERS:
        instance = registry.get(name)
        if name == "anthropic":
            configured = _anthropic_has_key()
        else:
            configured = instance is not None

        healthy = False
        if configured and instance is not None:
            try:
                healthy = await instance.health_check()
            except Exception:
                healthy = False

        out.append(
            {
                "name": name,
                "handles": stubs[name]["handles"],
                "avg_time_per_page": stubs[name]["avg_time_per_page"],
                "configured": configured,
                "healthy": healthy,
                "message": (
                    None
                    if configured
                    else (
                        "Paste your Anthropic API key to enable real extraction (no restart needed)."
                        if name == "anthropic"
                        else "API key not configured (Settings → add key)"
                    )
                ),
            }
        )
    return out


@router.post("/{name}/keys")
async def save_provider_keys(
    name: str,
    keys: dict[str, Any] = Body(...),
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> dict:
    # Validate the new keys before persisting. A failed validation still
    # saves the key (user may have no credits yet but want to retry later).
    is_valid = False
    validation_error: str | None = None
    try:
        if name == "anthropic":
            is_valid = await _validate_anthropic_key(keys.get("api_key", ""))
        else:
            provider = _instantiate_for_test(name, keys)
            is_valid = await provider.health_check()
    except Exception as e:
        validation_error = str(e)[:300]
        logger.warning("Key validation failed for provider=%s: %s", name, e)

    try:
        encrypted = encrypt(json.dumps(keys))
    except RuntimeError as e:
        raise HTTPException(
            500,
            detail=(
                "ENCRYPTION_KEY is not configured on the backend — keys cannot be "
                "saved. Generate one with "
                "`python -c 'from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())'`, add to .env, and restart "
                "the backend."
            ),
        ) from e

    existing = await session.execute(
        select(UserProviderKey).where(
            UserProviderKey.user_id == user_id,
            UserProviderKey.provider == name,
        )
    )
    row = existing.scalar_one_or_none()
    if row is None:
        row = UserProviderKey(
            user_id=user_id, provider=name, encrypted_keys=encrypted
        )
        session.add(row)
    else:
        row.encrypted_keys = encrypted

    await session.flush()

    # Invalidate the cached Anthropic client so the new key takes effect without
    # a restart. Also clears the @lru_cache of get_settings so
    # anthropic_use_mock re-reads state.
    if name == "anthropic":
        try:
            import app.core.claude_client as _cc

            _cc._client = None
            _cc._client_key = None
        except Exception:
            pass

    return {
        "saved": True,
        "valid": is_valid,
        "provider": name,
        "validation_error": validation_error,
    }


@router.get("/{name}/keys")
async def get_provider_key_status(
    name: str,
    user_id: UUID = Depends(get_current_user_id),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Does the user have keys saved for this provider? Never returns the key itself."""
    existing = await session.execute(
        select(UserProviderKey).where(
            UserProviderKey.user_id == user_id,
            UserProviderKey.provider == name,
        )
    )
    row = existing.scalar_one_or_none()
    return {"provider": name, "configured": row is not None}


@router.post("/test")
async def test_provider(body: dict = Body(...)) -> dict:
    """Run a health_check on the named provider using the registry."""
    name = body.get("name", "")
    registry = build_registry()
    provider = registry.get(name)
    if provider is None:
        raise HTTPException(404, detail=f"Provider not configured: {name}")

    start = time.time()
    healthy = await provider.health_check()
    elapsed = time.time() - start
    return {
        "provider": name,
        "healthy": healthy,
        "time_seconds": round(elapsed, 3),
    }


def load_user_keys_sync(session_sync, user_id: UUID, provider: str) -> dict | None:
    """Helper for workers: decrypt stored keys for a user+provider pair."""
    row = (
        session_sync.execute(
            select(UserProviderKey).where(
                UserProviderKey.user_id == user_id,
                UserProviderKey.provider == provider,
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        return None
    try:
        return json.loads(decrypt(row.encrypted_keys))
    except Exception as e:
        logger.warning("Failed to decrypt keys for user=%s provider=%s: %s", user_id, provider, e)
        return None
