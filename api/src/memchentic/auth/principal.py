from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from uuid import UUID

from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.models.entities import ApiKey, User, UserIdentity
from memchentic.settings import Settings, get_settings


@dataclass
class Principal:
    is_admin: bool = False
    user_id: UUID | None = None
    email: str | None = None
    display_name: str | None = None
    provider: str | None = None

    @property
    def subject(self) -> str:
        if self.is_admin and self.user_id is None:
            return "admin"
        return str(self.user_id) if self.user_id else "anonymous"


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def resolve_bearer(
    session: AsyncSession,
    token: str | None,
    settings: Settings | None = None,
) -> Principal | None:
    if not token:
        return None
    settings = settings or get_settings()

    if hmac.compare_digest(token.encode(), settings.memchentic_admin_api_key.encode()):
        return Principal(is_admin=True, email=settings.admin_contact_email, provider="admin")

    key_hash = hash_api_key(token)
    result = await session.execute(
        select(ApiKey, User)
        .join(User, User.id == ApiKey.user_id)
        .where(ApiKey.key_hash == key_hash, ApiKey.revoked_at.is_(None))
    )
    row = result.first()
    if row:
        _, user = row
        return Principal(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            provider=user.provider,
        )

    try:
        jwt.get_unverified_claims(token)
    except JWTError:
        return None

    try:
        from memchentic.auth.tokens import verify_access_token

        payload = await verify_access_token(session, token, settings)
    except Exception:  # noqa: BLE001 - any verify failure means an unauthenticated caller
        return None

    sub = payload.get("sub")
    if not sub:
        return None

    if sub == "admin":
        return Principal(is_admin=True, email=settings.admin_contact_email, provider="admin")

    try:
        user_id = UUID(sub)
    except ValueError:
        # provider subject style — look up any durable binding
        provider = payload.get("provider") or settings.oauth_provider
        result = await session.execute(
            select(User)
            .join(UserIdentity, UserIdentity.user_id == User.id)
            .where(
                UserIdentity.provider == provider,
                UserIdentity.provider_subject == sub,
            )
        )
        user = result.scalar_one_or_none()
        if not user:
            return None
        return Principal(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            provider=user.provider,
        )

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return None
    return Principal(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        provider=user.provider,
        is_admin=False,
    )


def check_admin_password(username: str, password: str, settings: Settings | None = None) -> bool:
    """Constant-time check of the env break-glass admin credentials.

    Login identifiers (admin username and admin email) are compared
    case-insensitively. The password remains case-sensitive.

    Args:
        username: Submitted admin username or ``MEMCHENTIC_ADMIN_EMAIL``.
        password: Submitted password.
        settings: Settings override; defaults to the cached settings.

    Returns:
        True when the identifier matches the admin user or email and the
        password matches.
    """
    settings = settings or get_settings()
    submitted = username.strip().casefold().encode()
    admin_user = settings.memchentic_admin_user.strip().casefold().encode()
    user_ok = hmac.compare_digest(submitted, admin_user)
    admin_email = settings.memchentic_admin_email.strip()
    # Keep a compare even when unset so timing stays closer across paths.
    email_target = admin_email.casefold().encode() if admin_email else admin_user
    email_ok = bool(admin_email) and hmac.compare_digest(submitted, email_target)
    pass_ok = hmac.compare_digest(password.encode(), settings.memchentic_admin_password.encode())
    return (user_ok or email_ok) and pass_ok
