"""OCR provider registry + router + fallback logic.

Provider credentials are sourced in this order:
  1. User-saved keys in ``user_provider_keys`` (encrypted at rest with Fernet)
  2. Environment / settings (dev convenience only; never commit these)

Missing credentials simply skip that provider — the registry still works with
just Anthropic, and ``extract_with_fallback`` degrades gracefully.

Notes on Anthropic:
  - The Anthropic provider does need ``ANTHROPIC_API_KEY`` configured on the
    backend process (this runs inside the Docker container, independent of
    any Claude Code session you may be in).
  - The key is not per-user and is never persisted in the DB — it's supplied
    via ``.env`` (which is gitignored) or your orchestrator's secret store.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from app.core.auth import DEV_USER_ID
from app.core.config import settings
from app.core.crypto import decrypt
from app.providers.anthropic import AnthropicProvider
from app.providers.base import OCRProvider

logger = logging.getLogger(__name__)

INDIC_LANGS = {"hi", "ta", "te", "bn", "mr", "gu", "kn", "ml", "pa"}


def _load_user_keys(session_sync, user_id: UUID, provider: str) -> dict[str, Any] | None:
    """Fetch + decrypt a user's saved keys for ``provider``. None if not configured."""
    # Deferred import to avoid circular deps during model registration.
    from sqlalchemy import select

    from app.models.user_provider_key import UserProviderKey

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
        logger.warning(
            "Failed to decrypt keys for user=%s provider=%s: %s", user_id, provider, e
        )
        return None


def _try_mathpix(keys: dict[str, Any] | None) -> OCRProvider | None:
    if not keys and not (settings.MATHPIX_APP_ID and settings.MATHPIX_APP_KEY):
        return None
    try:
        from app.providers.mathpix import MathpixProvider

        app_id = (keys or {}).get("app_id") or settings.MATHPIX_APP_ID
        app_key = (keys or {}).get("app_key") or settings.MATHPIX_APP_KEY
        return MathpixProvider(app_id, app_key)
    except Exception as e:
        logger.info("Mathpix unavailable (will fall back): %s", e)
        return None


def _try_sarvam(keys: dict[str, Any] | None) -> OCRProvider | None:
    if not keys and not settings.SARVAM_API_KEY:
        return None
    try:
        from app.providers.sarvam import SarvamProvider

        api_key = (keys or {}).get("api_key") or settings.SARVAM_API_KEY
        return SarvamProvider(api_key)
    except Exception as e:
        logger.info("Sarvam unavailable (will fall back): %s", e)
        return None


def _try_google_vision(keys: dict[str, Any] | None) -> OCRProvider | None:
    if not keys:
        return None
    try:
        from app.providers.google_vision import GoogleVisionProvider

        creds = keys.get("credentials_json")
        bucket = keys.get("gcs_bucket")
        if isinstance(creds, str):
            try:
                creds = json.loads(creds)
            except json.JSONDecodeError:
                logger.warning("google_vision credentials_json is not valid JSON")
                return None
        if not creds or not bucket:
            return None
        return GoogleVisionProvider(creds, bucket)
    except Exception as e:
        logger.info("GoogleVision unavailable (will fall back): %s", e)
        return None


def build_registry(
    *,
    session_sync=None,
    user_id: UUID | None = None,
) -> dict[str, OCRProvider]:
    """Build the available provider registry for a user.

    - Anthropic is always present (it uses the server-side ``ANTHROPIC_API_KEY``).
    - Mathpix / Sarvam / Google Vision are added only when the user has saved
      keys (or, for dev convenience, when env settings provide them).
    """
    registry: dict[str, OCRProvider] = {"anthropic": AnthropicProvider()}

    uid = user_id or DEV_USER_ID

    # Anthropic counts as "configured" if either the env key is set or the
    # user has saved a key in the DB. The presence of a user key affects the
    # /api/providers list UI ("Connected" vs "Not configured").
    # (The actual key resolution happens in claude_client.get_claude.)
    mathpix_keys = _load_user_keys(session_sync, uid, "mathpix") if session_sync else None
    sarvam_keys = _load_user_keys(session_sync, uid, "sarvam") if session_sync else None
    google_keys = (
        _load_user_keys(session_sync, uid, "google_vision") if session_sync else None
    )

    if provider := _try_mathpix(mathpix_keys):
        registry["mathpix"] = provider
    if provider := _try_sarvam(sarvam_keys):
        registry["sarvam"] = provider
    if provider := _try_google_vision(google_keys):
        registry["google_vision"] = provider

    return registry


def select_provider(
    registry: dict[str, OCRProvider], analyser_info: dict[str, Any]
) -> OCRProvider:
    """Pick the best provider based on Analyser metadata.

    Priority (per 05_OCR_PROVIDERS.md):
      1. Indic language → Sarvam
      2. Heavy equations → Mathpix
      3. Pure scanned PDF → Google Vision
      4. Default → Anthropic
    """
    language = (analyser_info.get("language") or "en")[:2].lower()
    pdf_type = analyser_info.get("pdf_type", "digital")
    has_equations = bool(analyser_info.get("has_equations"))
    equation_density = float(analyser_info.get("equation_density") or 0.0)

    if language in INDIC_LANGS and "sarvam" in registry:
        return registry["sarvam"]

    if has_equations and equation_density > 0.3 and "mathpix" in registry:
        return registry["mathpix"]

    if pdf_type == "scanned" and "google_vision" in registry:
        return registry["google_vision"]

    return registry["anthropic"]


async def extract_with_fallback(
    *,
    pdf_bytes: bytes,
    analyser_info: dict[str, Any],
    registry: dict[str, OCRProvider] | None = None,
) -> tuple[str, str]:
    """Run primary provider; fall back to Anthropic on failure.

    Returns ``(text, provider_name_used)``.
    """
    reg = registry or build_registry()
    primary = select_provider(reg, analyser_info)
    try:
        text = await primary.extract_text(pdf_bytes, analyser_info)
        return text, primary.name
    except Exception as e:
        if primary.name == "anthropic":
            raise
        logger.warning(
            "%s failed (%s: %s). Falling back to Anthropic.",
            primary.name,
            type(e).__name__,
            e,
        )
        fallback = reg["anthropic"]
        text = await fallback.extract_text(pdf_bytes, analyser_info)
        return text, fallback.name
