from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from memchentic.auth.passwords import hash_password, verify_password
from memchentic.models.entities import User, UserIdentity
from memchentic.services.spaces import ensure_personal_space


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
        raise ValueError("Email is required")
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


async def _ensure_identity(
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
        raise ValueError("Provider binding already belongs to another user")
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


async def register_internal_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    display_name: str | None = None,
) -> User:
    """Create an internal (email/password) user.

    Args:
        session: Database session.
        email: Required unique identity for the user.
        password: Plaintext password to hash.
        display_name: Optional display name; defaults to the local-part of email.

    Returns:
        The persisted user with a personal space.

    Raises:
        ValueError: If email is missing or already registered.
    """
    email_n = normalize_email(email)
    existing = await get_user_by_email(session, email_n)
    if existing:
        raise ValueError("Email already registered")
    user = User(
        id=uuid.uuid4(),
        email=email_n,
        password_hash=hash_password(password),
        provider="internal",
        provider_subject=email_n,
        display_name=display_name or email_n.split("@")[0],
    )
    session.add(user)
    await session.flush()
    await _ensure_identity(session, user, provider="internal", subject=email_n)
    await ensure_personal_space(session, user)
    await session.commit()
    await session.refresh(user)
    return user


async def authenticate_internal(
    session: AsyncSession, *, email: str, password: str
) -> User | None:
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
        return None
    user = await get_user_by_email(session, email_n)
    if not user or not user.password_hash:
        return None
    if not verify_password(password, user.password_hash):
        return None
    await _ensure_identity(session, user, provider="internal", subject=email_n)
    user.provider = "internal"
    user.provider_subject = email_n
    await session.commit()
    await session.refresh(user)
    return user


async def upsert_oauth_user(
    session: AsyncSession,
    *,
    provider: str,
    subject: str,
    email: str,
    display_name: str | None = None,
    email_verified: bool = True,
) -> User:
    """Find or create a user keyed by email; accumulate OAuth provider bindings.

    Email is the stable identity. Each successful IdP login adds (or refreshes)
    that provider's subject on the same user without removing other bindings,
    so switching ``OAUTH_PROVIDER`` or linking another IdP with the same
    verified email keeps prior logins working.

    Args:
        session: Database session.
        provider: Active OAuth provider name (e.g. ``google``, ``github``).
        subject: Provider-stable subject / user id claim.
        email: Required verified email from the IdP.
        display_name: Optional display name from the IdP profile.
        email_verified: Whether the IdP asserts the email is verified.

    Returns:
        The existing or newly created user.

    Raises:
        ValueError: If email is missing/unverified, or a provider binding
            conflict would orphan another account.
    """
    if not email_verified:
        raise ValueError("OAuth email must be verified")
    email_n = normalize_email(email)
    subject_n = subject.strip()
    if not subject_n:
        raise ValueError("OAuth subject is required")

    user = await get_user_by_provider_subject(session, provider=provider, subject=subject_n)
    if user:
        if user.email != email_n:
            clash = await get_user_by_email(session, email_n)
            if clash and clash.id != user.id:
                raise ValueError("Email already belongs to another user")
            user.email = email_n
        if display_name:
            user.display_name = display_name
        user.provider = provider
        user.provider_subject = subject_n
        await session.commit()
        await session.refresh(user)
        return user

    by_email = await get_user_by_email(session, email_n)
    if by_email:
        await _ensure_identity(session, by_email, provider=provider, subject=subject_n)
        by_email.provider = provider
        by_email.provider_subject = subject_n
        if display_name:
            by_email.display_name = display_name
        await session.commit()
        await session.refresh(by_email)
        return by_email

    user = User(
        id=uuid.uuid4(),
        email=email_n,
        password_hash=None,
        provider=provider,
        provider_subject=subject_n,
        display_name=display_name or email_n.split("@")[0],
    )
    session.add(user)
    await session.flush()
    await _ensure_identity(session, user, provider=provider, subject=subject_n)
    await ensure_personal_space(session, user)
    await session.commit()
    await session.refresh(user)
    return user


async def list_users(session: AsyncSession) -> list[User]:
    """List users newest-first for the directory.

    Args:
        session: Database session.

    Returns:
        All users ordered by created_at descending, with identities loaded.
    """
    result = await session.execute(
        select(User)
        .options(selectinload(User.identities))
        .order_by(User.created_at.desc())
    )
    return list(result.scalars())
