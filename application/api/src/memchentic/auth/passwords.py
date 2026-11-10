"""Password hashing with the bcrypt library (no passlib)."""

from __future__ import annotations

import bcrypt

DEFAULT_BCRYPT_ROUNDS = 12
"""Production bcrypt work factor."""

BCRYPT_ROUNDS = DEFAULT_BCRYPT_ROUNDS
"""Work factor for new hashes. Only the test configuration lowers it."""


def hash_password(password: str) -> str:
    """Hash a password with bcrypt.

    Args:
        password: Plaintext password.

    Returns:
        UTF-8 bcrypt hash string (``$2b$...``).
    """
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against a stored bcrypt hash.

    Args:
        password: Submitted plaintext.
        password_hash: Stored ``$2b$`` / ``$2a$`` hash.

    Returns:
        True when the password matches.
    """
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """Report whether a stored hash uses a work factor other than ``BCRYPT_ROUNDS``.

    Args:
        password_hash: Stored ``$2b$`` / ``$2a$`` hash.

    Returns:
        True when the hash is malformed or its cost differs from the current one.
    """
    parts = password_hash.split("$")
    try:
        return int(parts[2]) != BCRYPT_ROUNDS
    except (IndexError, ValueError):
        return True
