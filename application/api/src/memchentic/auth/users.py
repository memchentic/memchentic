"""User lookups: email normalisation, identity linking and internal login."""

from __future__ import annotations

import asyncio
import functools
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import bcrypt
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from memchentic.auth import passwords
from memchentic.auth.passwords import verify_password
from memchentic.models.entities import (
    ApiKey,
    AuthCode,
    AuthRefreshToken,
    GuiSession,
    User,
    UserIdentity,
)
from memchentic.services.errors import (
    ConflictError,
    ValidationError,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger("memchentic")


@functools.cache
def _dummy_hash(rounds: int) -> str:
    """Return a throwaway hash at ``rounds`` so missing-user auth pays the same verify cost."""
    return bcrypt.hashpw(b"memchentic-missing-user", bcrypt.gensalt(rounds=rounds)).decode()


def _missing_user_dummy_hash() -> str:
    """Return the timing-pad hash for the current work factor."""
    return _dummy_hash(passwords.BCRYPT_ROUNDS)


def _verify_against_dummy(password: str) -> bool:
    """Spend one verify on the timing-pad hash (call in a worker thread)."""
    return verify_password(password, _missing_user_dummy_hash())


def normalize_email(email: str) -> str:
    """Normalize an address used as the stable user identity.

    Args:
        email: Raw email from registration, admin create, or an IdP claim.

    Returns:
        Casefolded, trimmed email (ASCII-safe lowercase for typical addresses).

    Raises:
        ValueError: If the value is empty after trimming.
    """
    email_n = (email or "").strip().casefold()
    if not email_n:
        raise ValidationError("Email is required")
    return email_n


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    """Load a user by canonical email (case-insensitive).

    Args:
        session: Database session.
        email: Email to look up; normalized before query.

    Returns:
        The matching user, or None.
    """
    email_n = normalize_email(email)
    result = await session.execute(select(User).where(func.lower(User.email) == email_n))
    return result.scalar_one_or_none()


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    """Load a user by id with login bindings.

    Args:
        session: Database session.
        user_id: User primary key.

    Returns:
        The matching user, or None.
    """
    result = await session.execute(
        select(User).options(selectinload(User.identities)).where(User.id == user_id)
    )
    return result.scalar_one_or_none()


async def get_user_by_provider_subject(
    session: AsyncSession, *, provider: str, subject: str
) -> User | None:
    """Load a user by a durable provider binding.

    Args:
        session: Database session.
        provider: OAuth / internal provider name.
        subject: Provider-stable subject.

    Returns:
        The matching user, or None.
    """
    result = await session.execute(
        select(User)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .options(selectinload(User.identities))
        .where(UserIdentity.provider == provider, UserIdentity.provider_subject == subject)
    )
    return result.scalar_one_or_none()


def providers_for(user: User) -> list[str]:
    """Return sorted provider names linked to ``user``.

    Args:
        user: User that may have ``identities`` loaded.

    Returns:
        Distinct provider names, preferring loaded identities and falling back
        to the denormalized last-used provider.
    """
    if user.identities:
        return sorted({i.provider for i in user.identities})
    return [user.provider] if user.provider else []


async def ensure_identity(
    session: AsyncSession,
    user: User,
    *,
    provider: str,
    subject: str,
) -> None:
    """Attach or refresh a provider binding without dropping other IdPs.

    Args:
        session: Database session.
        user: Email-keyed user row.
        provider: Provider name to bind.
        subject: Provider-stable subject.

    Raises:
        ValueError: If the binding already belongs to a different user.
    """
    existing = await session.execute(
        select(UserIdentity).where(
            UserIdentity.provider == provider, UserIdentity.provider_subject == subject
        )
    )
    row = existing.scalar_one_or_none()
    if row and row.user_id != user.id:
        raise ConflictError("Provider binding already belongs to another user")
    if row:
        return

    same_provider = await session.execute(
        select(UserIdentity).where(
            UserIdentity.user_id == user.id, UserIdentity.provider == provider
        )
    )
    current = same_provider.scalar_one_or_none()
    if current:
        current.provider_subject = subject
        return

    session.add(
        UserIdentity(
            id=uuid.uuid4(),
            user_id=user.id,
            provider=provider,
            provider_subject=subject,
        )
    )


async def authenticate_internal(session: AsyncSession, *, email: str, password: str) -> User | None:
    """Authenticate an internal user by email and password.

    Args:
        session: Database session.
        email: Login email.
        password: Plaintext password.

    Returns:
        The user when credentials match, otherwise None.
    """
    try:
        email_n = normalize_email(email)
    except ValueError:
        await asyncio.to_thread(_verify_against_dummy, password)
        return None
    user = await get_user_by_email(session, email_n)
    if not user or not user.password_hash:
        await asyncio.to_thread(_verify_against_dummy, password)
        return None
    if not await asyncio.to_thread(verify_password, password, user.password_hash):
        return None
    await ensure_identity(session, user, provider="internal", subject=email_n)
    user.provider = "internal"
    user.provider_subject = email_n
    await session.commit()
    await session.refresh(user)
    return user


async def revoke_credentials_raw(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Revoke every stored credential of ``user_id`` (no ACL, no commit).

    Ends browser sessions, OAuth refresh tokens, unused authorization codes
    and API keys. ``api_keys`` is under row-level security: the caller must
    have an RLS context that can see the target's keys (admin, or the target
    user itself).

    Args:
        session: Database session.
        user_id: Account whose credentials are revoked.
    """
    now = datetime.now(UTC)
    await session.execute(
        update(GuiSession)
        .where(GuiSession.user_id == user_id, GuiSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await session.execute(
        update(AuthRefreshToken)
        .where(AuthRefreshToken.user_id == user_id, AuthRefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    await session.execute(
        update(AuthCode)
        .where(AuthCode.user_id == user_id, AuthCode.consumed_at.is_(None))
        .values(consumed_at=now)
    )
    await session.execute(
        update(ApiKey)
        .where(ApiKey.user_id == user_id, ApiKey.revoked_at.is_(None))
        .values(revoked_at=now)
    )
