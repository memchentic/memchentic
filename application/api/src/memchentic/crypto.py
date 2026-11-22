"""Symmetric encryption for secrets stored in Postgres (e.g. SMTP password)."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from memchentic.settings import Settings, get_settings

_PREFIX = "enc:v1:"


def _fernet(settings: Settings) -> Fernet:
    """Build a Fernet cipher from ``ENCRYPTION_KEY`` (SHA-256 → urlsafe key)."""
    digest = hashlib.sha256(settings.encryption_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str, settings: Settings | None = None) -> str:
    """Encrypt a short secret for storage; result is prefixed ``enc:v1:``.

    Args:
        plaintext: Secret to encrypt (may be empty).
        settings: Settings carrying ``ENCRYPTION_KEY``.

    Returns:
        Prefixed ciphertext safe to store in ``instance_settings.value``.
    """
    settings = settings or get_settings()
    token = _fernet(settings).encrypt(plaintext.encode("utf-8")).decode("ascii")
    return f"{_PREFIX}{token}"


def decrypt_secret(stored: str, settings: Settings | None = None) -> str:
    """Decrypt a value produced by :func:`encrypt_secret`.

    Args:
        stored: Prefixed ciphertext from Postgres.
        settings: Settings carrying ``ENCRYPTION_KEY``.

    Returns:
        The original plaintext.

    Raises:
        ValueError: When the value is not encrypted or cannot be decrypted.
    """
    settings = settings or get_settings()
    if not stored.startswith(_PREFIX):
        raise ValueError("Stored secret is not encrypted with enc:v1")
    token = stored[len(_PREFIX) :].encode("ascii")
    try:
        return _fernet(settings).decrypt(token).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError(
            "ENCRYPTION_KEY does not decrypt the stored secret. Restore the "
            "ENCRYPTION_KEY that was in use when the secret was saved."
        ) from exc


def is_encrypted_secret(stored: str) -> bool:
    """Return True when ``stored`` looks like an ``enc:v1:`` ciphertext."""
    return stored.startswith(_PREFIX)
