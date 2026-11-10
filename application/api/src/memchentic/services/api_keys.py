"""Per-user API keys: create, list, cycle and revoke."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import Principal, hash_api_key
from memchentic.db.session import advisory_xact_lock, commit_with_rls
from memchentic.models.entities import ApiKey
from memchentic.services import limits
from memchentic.services.errors import ForbiddenError, NotFoundError
from memchentic.services.validation import check_text


def _require_target(principal: Principal, user_id: uuid.UUID | None) -> uuid.UUID:
    """Resolve the user whose keys are being managed.

    Args:
        principal: Authenticated caller.
        user_id: Optional target; admins may set another user.

    Returns:
        The user id to act on.

    Raises:
        ForbiddenError: Missing target, or a non-admin targeting someone else.
    """
    if user_id is not None:
        if not principal.is_admin and principal.user_id != user_id:
            raise ForbiddenError("Cannot manage keys for other users")
        return user_id
    return principal.user_id


def _key_lock(user_id: uuid.UUID) -> str:
    """Advisory-lock name that serialises key issue/cycle for one user."""
    return f"memchentic:api-keys:{user_id}"


async def create_api_key(
    session: AsyncSession,
    principal: Principal,
    *,
    name: str,
    user_id: uuid.UUID | None = None,
) -> tuple[ApiKey, str]:
    """Issue a new API key for the caller (or another user when admin).

    Args:
        session: Database session.
        principal: Authenticated caller.
        name: Display name for the key.
        user_id: Optional target user; admins only.

    Returns:
        The persisted key row and the raw secret (shown once).
    """
    name = check_text(name.strip(), "name", limits.API_KEY_NAME_MAX)
    target = _require_target(principal, user_id)
    # Same lock as cycle_api_key, so a create never lands between a cycle's
    # "select active keys" and its commit.
    await advisory_xact_lock(session, _key_lock(target))
    raw = f"mc_{secrets.token_urlsafe(32)}"
    key = ApiKey(
        id=uuid.uuid4(),
        user_id=target,
        name=name.strip() or "default",
        key_prefix=raw[:10],
        key_hash=hash_api_key(raw),
    )
    session.add(key)
    await commit_with_rls(session, user_id=principal.user_id, is_admin=principal.is_admin)
    await session.refresh(key)
    return key, raw


async def list_api_keys(session: AsyncSession, principal: Principal) -> list[ApiKey]:
    """List active API keys.

    Non-admins see only their own keys. Admins see every active key.

    Args:
        session: Database session.
        principal: Authenticated caller.

    Returns:
        Active (non-revoked) keys, newest first.
    """
    stmt = select(ApiKey).where(ApiKey.revoked_at.is_(None)).order_by(ApiKey.created_at.desc())
    if not principal.is_admin:
        stmt = stmt.where(ApiKey.user_id == principal.user_id)
    result = await session.execute(stmt)
    return list(result.scalars())


async def cycle_api_key(
    session: AsyncSession,
    principal: Principal,
    *,
    user_id: uuid.UUID | None = None,
    name: str = "default",
) -> tuple[ApiKey, str]:
    """Revoke a user's active keys and issue one new random secret.

    Args:
        session: Database session.
        principal: Authenticated caller.
        user_id: Optional target; admins may cycle another user.
        name: Display name for the replacement key.

    Returns:
        The new key row and raw secret (shown once).
    """
    name = check_text(name.strip(), "name", limits.API_KEY_NAME_MAX)
    target = _require_target(principal, user_id)
    # Serialise cycles (and creates) for this user: the SELECT below then sees
    # every key an earlier overlapping call committed, and revokes it.
    await advisory_xact_lock(session, _key_lock(target))
    now = datetime.now(UTC)
    result = await session.execute(
        select(ApiKey).where(ApiKey.user_id == target, ApiKey.revoked_at.is_(None))
    )
    for existing in result.scalars():
        existing.revoked_at = now

    raw = f"mc_{secrets.token_urlsafe(32)}"
    key = ApiKey(
        id=uuid.uuid4(),
        user_id=target,
        name=name.strip() or "default",
        key_prefix=raw[:10],
        key_hash=hash_api_key(raw),
    )
    session.add(key)
    await commit_with_rls(session, user_id=principal.user_id, is_admin=principal.is_admin)
    await session.refresh(key)
    return key, raw


async def revoke_api_key(session: AsyncSession, principal: Principal, key_id: uuid.UUID) -> None:
    """Revoke one API key.

    Args:
        session: Database session.
        principal: Authenticated caller.
        key_id: Key to revoke.

    Raises:
        NotFoundError: Missing key, or a non-admin revoking someone else's.
    """
    result = await session.execute(select(ApiKey).where(ApiKey.id == key_id))
    key = result.scalar_one_or_none()
    if not key:
        raise NotFoundError("Key not found")
    if not principal.is_admin and key.user_id != principal.user_id:
        raise NotFoundError("Key not found")
    key.revoked_at = datetime.now(UTC)
    await commit_with_rls(session, user_id=principal.user_id, is_admin=principal.is_admin)
