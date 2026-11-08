"""Database-backed browser sessions behind the GUI's HttpOnly cookie.

Sessions live in Postgres, so any api replica can refresh them; load
balancers need no session affinity.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.tokens import hash_token, new_opaque_token
from memchentic.models.entities import GuiSession
from memchentic.settings import Settings

SESSION_COOKIE = "memchentic_session"
SESSION_COOKIE_PATH = "/api/auth"


def session_lifetime(persistent: bool, settings: Settings) -> timedelta:
    """Return how long a session stays valid from creation or last use.

    Args:
        persistent: True for "remember me" sessions.
        settings: App settings.

    Returns:
        Session lifetime.
    """
    if persistent:
        return timedelta(days=settings.session_remember_days)
    return timedelta(hours=settings.session_browser_hours)


async def create_session(
    session: AsyncSession,
    *,
    subject: str,
    email: str | None,
    provider: str,
    user_id: uuid.UUID | None,
    persistent: bool,
    settings: Settings,
) -> tuple[str, GuiSession]:
    """Create a browser session and return its raw cookie token.

    Args:
        session: Database session.
        subject: JWT subject for access tokens minted from this session.
        email: Email claim for minted access tokens.
        provider: Provider claim for minted access tokens.
        user_id: Owning user, or None for the admin break-glass principal.
        persistent: True for "remember me" sessions.
        settings: App settings.

    Returns:
        Tuple of (raw token for the cookie, stored session row).
    """
    raw = new_opaque_token()
    row = GuiSession(
        id=uuid.uuid4(),
        token_hash=hash_token(raw),
        subject=subject,
        email=email,
        provider=provider,
        user_id=user_id,
        persistent=persistent,
        expires_at=datetime.now(UTC) + session_lifetime(persistent, settings),
    )
    session.add(row)
    await session.commit()
    return raw, row


async def touch_session(
    session: AsyncSession, raw: str, settings: Settings
) -> GuiSession | None:
    """Validate a cookie token and slide a remembered session's expiry.

    Browser-only sessions keep their original expiry so they cannot be kept
    alive indefinitely.

    Args:
        session: Database session.
        raw: Raw cookie token.
        settings: App settings.

    Returns:
        The live session, or None when unknown, revoked, or expired.
    """
    result = await session.execute(
        select(GuiSession).where(
            GuiSession.token_hash == hash_token(raw), GuiSession.revoked_at.is_(None)
        )
    )
    row = result.scalar_one_or_none()
    now = datetime.now(UTC)
    if row is None or row.expires_at <= now:
        return None
    row.last_used_at = now
    if row.persistent:
        row.expires_at = now + session_lifetime(True, settings)
    await session.commit()
    return row


async def revoke_session(session: AsyncSession, raw: str) -> None:
    """Revoke the session for a cookie token, if any.

    Args:
        session: Database session.
        raw: Raw cookie token.
    """
    result = await session.execute(
        select(GuiSession).where(
            GuiSession.token_hash == hash_token(raw), GuiSession.revoked_at.is_(None)
        )
    )
    row = result.scalar_one_or_none()
    if row is not None:
        row.revoked_at = datetime.now(UTC)
        await session.commit()
