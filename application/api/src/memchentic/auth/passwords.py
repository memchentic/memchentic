"""Password hashing with the bcrypt library (no passlib)."""

from __future__ import annotations

import bcrypt

DEFAULT_BCRYPT_ROUNDS = 12
"""Production bcrypt work factor."""

BCRYPT_ROUNDS = DEFAULT_BCRYPT_ROUNDS
"""Work factor for new hashes. Only the test configuration lowers it."""

# bcrypt uses at most 72 bytes of input. bcrypt 4 cut longer input silently;
# bcrypt 5 raises. The checks below make both versions behave the same.
MAX_PASSWORD_BYTES = 72


def password_too_long(password: str) -> bool:
    """Report whether a password exceeds the bcrypt input limit.

    Args:
        password: Plaintext password.

    Returns:
        True when the UTF-8 encoding is longer than ``MAX_PASSWORD_BYTES``.
    """
    return len(password.encode("utf-8")) > MAX_PASSWORD_BYTES


def require_bcrypt_length(password: str, setting_name: str) -> None:
    """Refuse a configured password that bcrypt cannot hash.

    Args:
        password: Value of the setting.
        setting_name: Environment variable name used in the error message.

    Raises:
        RuntimeError: When the value is longer than ``MAX_PASSWORD_BYTES`` bytes.
    """
    if password_too_long(password):
        raise RuntimeError(
            f"Refusing to start: {setting_name} is longer than {MAX_PASSWORD_BYTES} bytes "
            f"(the bcrypt limit). Set a value of at most {MAX_PASSWORD_BYTES} bytes."
        )


def hash_password(password: str) -> str:
    """Hash a password with bcrypt.

    Args:
        password: Plaintext password.

    Returns:
        UTF-8 bcrypt hash string (``$2b$...``).

    Raises:
        ValueError: When the password is longer than ``MAX_PASSWORD_BYTES`` bytes.
    """
    if password_too_long(password):
        raise ValueError(f"password is longer than {MAX_PASSWORD_BYTES} bytes")
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against a stored bcrypt hash.

    Args:
        password: Submitted plaintext.
        password_hash: Stored ``$2b$`` / ``$2a$`` hash.

    Returns:
        True when the password matches. A password longer than
        ``MAX_PASSWORD_BYTES`` bytes never matches.
    """
    if password_too_long(password):
        return False
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
