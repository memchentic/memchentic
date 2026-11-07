from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import Principal, hash_api_key
from memchentic.models.entities import ApiKey


async def create_api_key(
    session: AsyncSession,
    principal: Principal,
    *,
    name: str,
    user_id: uuid.UUID | None = None,
) -> tuple[ApiKey, str]:
    target = user_id or principal.user_id
    if principal.is_admin and user_id:
        target = user_id
    if not target:
        raise PermissionError("user_id required")
    if not principal.is_admin and principal.user_id != target:
        raise PermissionError("Cannot create keys for other users")

    raw = f"mc_{secrets.token_urlsafe(32)}"
    key = ApiKey(
        id=uuid.uuid4(),
        user_id=target,
        name=name.strip() or "default",
        key_prefix=raw[:10],
        key_hash=hash_api_key(raw),
    )
    session.add(key)
    await session.commit()
    await session.refresh(key)
    return key, raw


async def list_api_keys(
    session: AsyncSession, principal: Principal, user_id: uuid.UUID | None = None
) -> list[ApiKey]:
    target = user_id or principal.user_id
    if principal.is_admin and user_id is None and principal.user_id is None:
        result = await session.execute(
            select(ApiKey).where(ApiKey.revoked_at.is_(None)).order_by(ApiKey.created_at.desc())
        )
        return list(result.scalars())
    if not target:
        return []
    if not principal.is_admin and principal.user_id != target:
        raise PermissionError("Cannot list other users' keys")
    result = await session.execute(
        select(ApiKey)
        .where(ApiKey.user_id == target, ApiKey.revoked_at.is_(None))
        .order_by(ApiKey.created_at.desc())
    )
    return list(result.scalars())


async def revoke_api_key(
    session: AsyncSession, principal: Principal, key_id: uuid.UUID
) -> None:
    result = await session.execute(select(ApiKey).where(ApiKey.id == key_id))
    key = result.scalar_one_or_none()
    if not key:
        raise PermissionError("Key not found")
    if not principal.is_admin and key.user_id != principal.user_id:
        raise PermissionError("Cannot revoke other users' keys")
    key.revoked_at = datetime.now(UTC)
    await session.commit()
