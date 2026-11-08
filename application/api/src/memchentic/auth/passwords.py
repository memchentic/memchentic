"""Password hashing with the bcrypt library (no passlib)."""

from __future__ import annotations

import bcrypt


def hash_password(password: str) -> str:
    """Hash a password with bcrypt.

    Args:
        password: Plaintext password.

    Returns:
        UTF-8 bcrypt hash string (``$2b$...``).
    """
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


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
