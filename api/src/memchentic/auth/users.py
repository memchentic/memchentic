from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.passwords import hash_password, verify_password
from memchentic.models.entities import User
from memchentic.services.spaces import ensure_personal_space


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email.lower()))
    return result.scalar_one_or_none()


async def register_internal_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    display_name: str | None = None,
) -> User:
    email_n = email.lower().strip()
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
    await ensure_personal_space(session, user)
    await session.commit()
    await session.refresh(user)
    return user


async def authenticate_internal(
    session: AsyncSession, *, email: str, password: str
) -> User | None:
    user = await get_user_by_email(session, email.lower().strip())
    if not user or not user.password_hash:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


async def upsert_oauth_user(
    session: AsyncSession,
    *,
    provider: str,
    subject: str,
    email: str,
    display_name: str | None = None,
) -> User:
    result = await session.execute(
        select(User).where(User.provider == provider, User.provider_subject == subject)
    )
    user = result.scalar_one_or_none()
    if user:
        if email and user.email != email.lower():
            user.email = email.lower()
        if display_name:
            user.display_name = display_name
        await session.commit()
        await session.refresh(user)
        return user

    email_n = (email or f"{subject}@{provider}.oauth").lower()
    # If email taken by another provider binding, uniquify
    clash = await get_user_by_email(session, email_n)
    if clash:
        email_n = f"{subject}.{provider}@users.memchentic.local"

    user = User(
        id=uuid.uuid4(),
        email=email_n,
        password_hash=None,
        provider=provider,
        provider_subject=subject,
        display_name=display_name or email_n.split("@")[0],
    )
    session.add(user)
    await session.flush()
    await ensure_personal_space(session, user)
    await session.commit()
    await session.refresh(user)
    return user


async def list_users(session: AsyncSession) -> list[User]:
    result = await session.execute(select(User).order_by(User.created_at.desc()))
    return list(result.scalars())
