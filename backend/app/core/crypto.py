"""Symmetric encryption for user-supplied API keys (Fernet, key in env)."""

from __future__ import annotations

import logging

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

logger = logging.getLogger(__name__)


def _fernet() -> Fernet:
    if not settings.ENCRYPTION_KEY:
        raise RuntimeError(
            "ENCRYPTION_KEY is not configured — generate one with "
            "`python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`"
        )
    return Fernet(settings.ENCRYPTION_KEY.encode())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise ValueError("Invalid or tampered ciphertext") from e
