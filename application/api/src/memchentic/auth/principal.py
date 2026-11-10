"""Request principals, API-key hashing and the environment admin password check."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from uuid import UUID

from memchentic.settings import Settings, get_settings


@dataclass
class Principal:
    """An authenticated caller. Every principal is backed by a ``User`` row."""

    user_id: UUID
    is_admin: bool = False
    email: str | None = None
    display_name: str | None = None
    provider: str | None = None

    def __post_init__(self) -> None:
        """Reject a principal without a user id.

        Raises:
            TypeError: When ``user_id`` is ``None``.
        """
        if self.user_id is None:
            raise TypeError("Principal requires a user id")

    @property
    def subject(self) -> str:
        """Return the user id as text."""
        return str(self.user_id)


def bearer_token_from_header(value: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header value.

    Args:
        value: Raw header value, or ``None`` when the header is absent.

    Returns:
        The token, or ``None`` when the header is absent, not Bearer, or empty.
    """
    if value and value.lower().startswith("bearer "):
        return value[7:].strip() or None
    return None


def check_admin_password(email: str, password: str, settings: Settings | None = None) -> bool:
    """Constant-time check of the ``ADMIN_EMAIL`` / ``ADMIN_PASSWORD`` credentials.

    The login identity is ``ADMIN_EMAIL`` only (case-
    insensitive). ``ADMIN_DISPLAY_NAME`` is a label, not a login
    alias. The password remains case-sensitive.

    Args:
        email: Submitted email (must match ``ADMIN_EMAIL``).
        password: Submitted password.
        settings: Settings override; defaults to the cached settings.

    Returns:
        True when the email matches the admin email and the password matches.
    """
    settings = settings or get_settings()
    submitted = email.strip().casefold().encode()
    email_target = settings.admin_contact_email.casefold().encode()
    email_ok = hmac.compare_digest(submitted, email_target)
    pass_ok = hmac.compare_digest(password.encode(), settings.admin_password.encode())
    return email_ok and pass_ok
