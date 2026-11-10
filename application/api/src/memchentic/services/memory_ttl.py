"""Entry lifetimes: expiry resolution, sliding TTL and active-row filtering."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import DateTime, Select, and_, case, func, literal, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import Principal
from memchentic.db.session import commit_with_rls
from memchentic.models.entities import Memory
from memchentic.search.hybrid import Digest, active_memory_clause
from memchentic.services.errors import ValidationError
from memchentic.services.validation import check_ttl_days
from memchentic.settings import get_settings


def check_lifetime_args(
    *, expires_at: datetime | None, ttl_days: int | None, permanent: bool
) -> None:
    """Reject contradictory lifetime arguments.

    Args:
        expires_at: Absolute expiry supplied by the caller.
        ttl_days: Relative lifetime in days; ``0`` means permanent.
        permanent: Caller wants no expiry.

    Raises:
        ValidationError: When ``ttl_days`` is outside
            ``0..limits.TTL_DAYS_MAX`` or the arguments contradict each other.
    """
    check_ttl_days(ttl_days)
    if expires_at is not None and (permanent or ttl_days is not None):
        raise ValidationError("Pass only one of expires_at, ttl_days or permanent")
    if permanent and ttl_days:
        raise ValidationError("permanent=true contradicts ttl_days > 0; pass only one")


def resolve_expiry(
    *,
    expires_at: datetime | None,
    ttl_days: int | None,
    permanent: bool,
    now: datetime | None = None,
) -> datetime | None:
    """Pick the create-time expiry for a new memory.

    At most one of ``expires_at``, ``ttl_days`` and ``permanent`` may be set
    (``permanent`` with ``ttl_days=0`` is allowed: both mean no expiry). With
    none set the lifetime is ``MEMORY_DEFAULT_TTL_DAYS``.

    Args:
        expires_at: Absolute expiry supplied by the caller.
        ttl_days: Relative lifetime in days; ``0`` means permanent.
        permanent: Caller wants no expiry.
        now: Reference time; defaults to the current UTC time.

    Returns:
        Expiry timestamp, or None for a permanent memory.

    Raises:
        ValidationError: When ``ttl_days`` is outside
            ``0..limits.TTL_DAYS_MAX`` or the arguments contradict each other.
    """
    check_lifetime_args(expires_at=expires_at, ttl_days=ttl_days, permanent=permanent)
    if expires_at is not None:
        return expires_at
    if permanent or ttl_days == 0:
        return None
    ref = now or datetime.now(UTC)
    days = ttl_days if ttl_days is not None else get_settings().memory_default_ttl_days
    return ref + timedelta(days=days) if days > 0 else None


def slide_expiry(
    current: datetime | None, *, created_at: datetime | None, now: datetime
) -> datetime | None:
    """Push ``current`` out to ``now + MEMORY_TTL_EXTEND_DAYS`` without shortening it.

    The extension is capped at ``created_at + MEMORY_MAX_TTL_DAYS`` when that
    ceiling is enabled. Permanent memories (``None``) stay permanent.

    Args:
        current: Existing expiry.
        created_at: Row creation time for the ceiling.
        now: Reference time.

    Returns:
        New expiry.
    """
    if current is None:
        return None
    settings = get_settings()
    target = now + timedelta(days=settings.memory_ttl_extend_days)
    if settings.memory_max_ttl_days > 0 and created_at is not None:
        target = min(target, created_at + timedelta(days=settings.memory_max_ttl_days))
    return max(current, target)


async def touch_memories(
    session: AsyncSession, principal: Principal, ids: Sequence[str | uuid.UUID]
) -> None:
    """Record a recall of ``ids``: bump usage and slide live expiries.

    At most one touch per memory per ``MEMORY_TTL_TOUCH_DEBOUNCE_HOURS``.
    Past-due memories are counted but not revived. ``updated_at`` is preserved
    so recall does not reset edit recency.

    Args:
        session: Database session.
        principal: Caller (for RLS rebinding on commit).
        ids: Memory ids that were returned or opened.
    """
    uids = [i if isinstance(i, uuid.UUID) else uuid.UUID(str(i)) for i in ids]
    if not uids:
        return
    settings = get_settings()
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=settings.memory_ttl_touch_debounce_hours)
    target: Any = literal(
        now + timedelta(days=settings.memory_ttl_extend_days), DateTime(timezone=True)
    )
    if settings.memory_max_ttl_days > 0:
        target = func.least(
            target, Memory.created_at + timedelta(days=settings.memory_max_ttl_days)
        )
    slid = case(
        (
            and_(Memory.expires_at.is_not(None), Memory.expires_at > now),
            func.greatest(Memory.expires_at, target),
        ),
        else_=Memory.expires_at,
    )
    await session.execute(
        update(Memory)
        .where(
            Memory.id.in_(uids),
            or_(Memory.last_recalled_at.is_(None), Memory.last_recalled_at < cutoff),
        )
        .values(
            recall_count=Memory.recall_count + 1,
            last_recalled_at=now,
            expires_at=slid,
            updated_at=Memory.updated_at,
        )
        .execution_options(synchronize_session=False)
    )
    await commit_with_rls(session, user_id=principal.user_id, is_admin=principal.is_admin)


async def touch_digests(
    session: AsyncSession, principal: Principal, digests: list[Digest]
) -> list[Digest]:
    await touch_memories(session, principal, [d.id for d in digests])
    return digests


def active_select(space_ids: list[uuid.UUID], include_expired: bool) -> Select[Memory]:
    """Memories in ``space_ids``, hiding past-due rows unless ``include_expired``."""
    stmt = select(Memory).where(Memory.space_id.in_(space_ids))
    active = active_memory_clause(include_expired=include_expired)
    if active is not None:
        stmt = stmt.where(active)
    return stmt
