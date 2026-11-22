"""Registration confirmation tokens and gating helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.tokens import hash_token, new_opaque_token
from memchentic.auth.users import get_user_by_email, normalize_email
from memchentic.models.entities import EmailVerificationToken, User
from memchentic.services.instance_settings import is_mail_configured
from memchentic.services.mail import send_registration_confirm

TOKEN_LIFETIME = timedelta(hours=24)


async def must_confirm_email(session: AsyncSession, user: User) -> bool:
    """True when mail is configured and this user has not confirmed their email.

    Admin break-glass and IdP principals are not gated here; callers that
    resolve those paths never call this with an unverified internal user that
    should still work (admins are always verified at ensure time).
    """
    if user.email_verified:
        return False
    return await is_mail_configured(session)


async def issue_verification_token(
    session: AsyncSession,
    user: User,
    *,
    send: bool = True,
) -> str:
    """Create a one-time confirmation token for ``user`` and optionally email it.

    Prior unused tokens for the user are consumed. The caller commits.

    Args:
        session: Database session.
        user: Account that must confirm.
        send: When True, attempt to email the link (best-effort).

    Returns:
        The raw token (only available to the caller / email).
    """
    now = datetime.now(UTC)
    await session.execute(
        update(EmailVerificationToken)
        .where(
            EmailVerificationToken.user_id == user.id,
            EmailVerificationToken.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )
    raw = new_opaque_token()
    session.add(
        EmailVerificationToken(
            id=uuid.uuid4(),
            token_hash=hash_token(raw),
            user_id=user.id,
            expires_at=now + TOKEN_LIFETIME,
        )
    )
    await session.flush()
    if send:
        await send_registration_confirm(session, to=user.email, token=raw)
    return raw


async def consume_verification_token(session: AsyncSession, raw_token: str) -> User | None:
    """Mark a confirmation token used and set ``user.email_verified``.

    Args:
        session: Database session.
        raw_token: Opaque token from the confirmation link.

    Returns:
        The verified user, or None when the token is missing/expired/used.
    """
    token_hash = hash_token(raw_token.strip())
    now = datetime.now(UTC)
    result = await session.execute(
        update(EmailVerificationToken)
        .where(
            EmailVerificationToken.token_hash == token_hash,
            EmailVerificationToken.consumed_at.is_(None),
            EmailVerificationToken.expires_at > now,
        )
        .values(consumed_at=now)
        .returning(EmailVerificationToken.user_id)
    )
    row = result.first()
    if row is None:
        return None
    user = await session.get(User, row.user_id)
    if user is None:
        return None
    user.email_verified = True
    await session.commit()
    await session.refresh(user)
    return user


async def resend_verification(session: AsyncSession, email: str) -> None:
    """Re-issue a confirmation email when the account is unverified and mail is on.

    Always a no-op from the caller's perspective (no enumeration). Commits when
    a token is issued.
    """
    if not await is_mail_configured(session):
        return
    try:
        email_n = normalize_email(email)
    except ValueError:
        return
    user = await get_user_by_email(session, email_n)
    if user is None or user.email_verified:
        return
    await issue_verification_token(session, user, send=True)
    await session.commit()
