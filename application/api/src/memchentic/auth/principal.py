"""Request principals, API-key hashing and the break-glass admin password check."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from uuid import UUID

from memchentic.settings import Settings, get_settings


@dataclass
class Principal:
    """Authenticated caller resolved from a bearer credential."""

    is_admin: bool = False
    user_id: UUID | None = None
    email: str | None = None
    display_name: str | None = None
    provider: str | None = None

    @property
    def subject(self) -> str:
        """Return the user id as text, or ``anonymous``."""
        return str(self.user_id) if self.user_id else "anonymous"


def hash_api_key(raw: str) -> str:
    """Return the SHA-256 hex digest stored for an API key."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def check_admin_password(email: str, password: str, settings: Settings | None = None) -> bool:
    """Constant-time check of the env break-glass admin credentials.

    Break-glass login identity is ``ADMIN_EMAIL`` only (case-
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
