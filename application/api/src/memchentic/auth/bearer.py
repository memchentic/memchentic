"""Bearer credential resolution: admin key, API key, session JWT, IdP JWT."""

from __future__ import annotations

import hmac
from uuid import UUID

import jwt
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.claims import groups_from_claims
from memchentic.auth.principal import Principal, hash_api_key
from memchentic.auth.tokens import verify_access_token
from memchentic.auth.users import get_user_by_email
from memchentic.models.entities import User, UserIdentity
from memchentic.services.auth_roster import ensure_auth_groups, user_is_administrator
from memchentic.services.users import ensure_admin_user, upsert_oauth_user
from memchentic.settings import Settings, get_settings


async def resolve_bearer(
    session: AsyncSession,
    token: str | None,
    settings: Settings | None = None,
) -> Principal | None:
    """Resolve a bearer token to a principal.

    Accepts ``ADMIN_API_KEY``, a per-user API key, an internal access token,
    or (in external-IdP mode) an IdP token.

    Args:
        session: Database session.
        token: Raw bearer token, or None.
        settings: Optional settings override.

    Returns:
        The principal, or None when the token is missing or invalid.
    """
    if not token:
        return None
    settings = settings or get_settings()

    if hmac.compare_digest(token.encode(), settings.admin_api_key.encode()):
        # The application lifespan ensures the admin row and the rosters at
        # start. Per request, one SELECT is enough.
        admin = await get_user_by_email(session, settings.admin_contact_email)
        if admin is None:
            # The row was removed out of band: rebuild it once.
            admin = await ensure_admin_user(session, settings)
            await ensure_auth_groups(session, admin)
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
        return Principal(
            user_id=key_row.user_id,
            email=key_row.email,
            display_name=key_row.display_name,
            provider=key_row.provider,
            is_admin=await user_is_administrator(session, key_row.user_id),
        )

    try:
        payload = await verify_access_token(session, token, settings)
    except (jwt.PyJWTError, ValueError):
        # The token is malformed, expired, wrongly signed or fails a claim
        # check. Database and JWKS transport errors are not caught: they
        # propagate, the request fails with a 5xx, and the caller keeps its
        # token.
        return None

    sub = str(payload.get("sub") or "").strip()
    if not sub:
        return None

    # The verified issuer says who signed the token. A memchentic-signed token
    # carries ``session_issuer`` and a local user id in ``sub``. Every other
    # verified token was signed by the external IdP; its ``sub`` is an opaque
    # string that is only ever matched against ``user_identities`` under the
    # configured provider, whatever its shape and whatever the token claims.
    if payload.get("iss") == settings.session_issuer:
        try:
            user_id = UUID(sub)
        except ValueError:
            return None
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
    else:
        if settings.oauth_provider == "internal":
            return None
        provider = settings.oauth_provider
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
            user = await _upsert_from_external_claims(
                session, payload, provider=provider, subject=sub
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
