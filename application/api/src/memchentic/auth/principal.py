from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from uuid import UUID

import jwt
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.models.entities import User, UserIdentity
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

    if hmac.compare_digest(token.encode(), settings.admin_api_key.encode()):
        from memchentic.auth.users import ensure_admin_user
        from memchentic.services.auth_groups import ensure_auth_groups

        admin = await ensure_admin_user(session, settings)
        await ensure_auth_groups(session, settings)
        return Principal(
            is_admin=True,
            user_id=admin.id,
            email=admin.email,
            display_name=admin.display_name,
            provider="admin",
        )

    # API-key lookup runs before set_rls_context; RLS would hide every row.
    key_hash = hash_api_key(token)
    key_row = (
        await session.execute(
            text("SELECT user_id, email, display_name, provider FROM app_resolve_api_key(:h)"),
            {"h": key_hash},
        )
    ).first()
    if key_row:
        from memchentic.services.auth_groups import user_is_administrator

        return Principal(
            user_id=key_row.user_id,
            email=key_row.email,
            display_name=key_row.display_name,
            provider=key_row.provider,
            is_admin=await user_is_administrator(session, key_row.user_id),
        )

    try:
        # Local import on purpose: tests patch
        # ``memchentic.auth.tokens.verify_access_token``.
        from memchentic.auth.tokens import verify_access_token

        payload = await verify_access_token(session, token, settings)
    except (jwt.PyJWTError, ValueError):
        # The token is malformed, expired, wrongly signed or fails a claim
        # check. Database and JWKS transport errors are not caught: they
        # propagate, the request fails with a 5xx, and the caller keeps its
        # token.
        return None

    sub = payload.get("sub")
    if not sub:
        return None

    from memchentic.services.auth_groups import user_is_administrator

    try:
        user_id = UUID(sub)
    except ValueError:
        # provider subject style — look up any durable binding; provision on first hit
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
        if not user and settings.oauth_provider != "internal":
            user = await _upsert_from_external_claims(
                session, payload, provider=provider, subject=str(sub)
            )
        if not user:
            return None
        return Principal(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            provider=user.provider,
            is_admin=await user_is_administrator(session, user.id),
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
        is_admin=await user_is_administrator(session, user.id),
    )


async def _upsert_from_external_claims(
    session: AsyncSession,
    payload: dict,
    *,
    provider: str,
    subject: str,
) -> User | None:
    """Create or link a user from verified email claims on a first IdP JWT.

    Args:
        session: Database session.
        payload: Verified JWT claims.
        provider: Active OAuth provider name.
        subject: Provider subject claim.

    Returns:
        The upserted user, or ``None`` when email is missing/unverified.
    """
    from memchentic.auth.idp_groups import groups_from_claims
    from memchentic.auth.users import upsert_oauth_user

    email = payload.get("email")
    if not isinstance(email, str) or not email.strip():
        return None
    raw_verified = payload.get("email_verified", False)
    if isinstance(raw_verified, str):
        verified = raw_verified.lower() in {"1", "true", "yes"}
    else:
        verified = bool(raw_verified)
    if not verified:
        return None
    display = payload.get("name") or payload.get("preferred_username")
    try:
        return await upsert_oauth_user(
            session,
            provider=provider,
            subject=subject,
            email=email,
            display_name=str(display) if display else None,
            email_verified=True,
            idp_groups=groups_from_claims(payload),
        )
    except ValueError:
        return None


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
