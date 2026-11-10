"""Per-user API key management."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.api.schemas import ApiKeyCycleIn, ApiKeyIn
from memchentic.auth.principal import Principal
from memchentic.deps import db_session, get_principal
from memchentic.services import api_keys as api_key_service
from memchentic.services.validation import parse_uuid

router = APIRouter()


@router.get("/api-keys")
async def list_keys(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> list[dict]:
    """List active keys: own for users, all accounts for admins."""
    keys = await api_key_service.list_api_keys(session, principal)
    return [
        {
            "id": str(k.id),
            "name": k.name,
            "prefix": k.key_prefix,
            "created_at": k.created_at.isoformat() if k.created_at else None,
            "user_id": str(k.user_id),
        }
        for k in keys
    ]


@router.post("/api-keys")
async def create_key(
    body: ApiKeyIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Create an API key and return the raw key once.

    Users create keys for themselves; administrators may pass ``user_id``.
    Errors: 403 when creating a key for another user without admin rights.
    """
    key, raw = await api_key_service.create_api_key(
        session, principal, name=body.name, user_id=parse_uuid(body.user_id, "user_id")
    )
    return {
        "id": str(key.id),
        "name": key.name,
        "prefix": key.key_prefix,
        "key": raw,
        "note": "Copy now; the secret is not shown again.",
    }


@router.post("/api-keys/cycle")
async def cycle_key(
    body: ApiKeyCycleIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Revoke the target user's active keys and issue one new random secret."""
    key, raw = await api_key_service.cycle_api_key(
        session, principal, user_id=parse_uuid(body.user_id, "user_id"), name=body.name
    )
    return {
        "id": str(key.id),
        "name": key.name,
        "prefix": key.key_prefix,
        "user_id": str(key.user_id),
        "key": raw,
        "note": "Copy now; the secret is not shown again. Prior keys for this user are revoked.",
    }


@router.delete("/api-keys/{key_id}")
async def revoke_key(
    key_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Revoke an API key. Its owner or an administrator.

    Errors: 403 when not allowed, 404 when the key does not exist.
    """
    await api_key_service.revoke_api_key(session, principal, key_id)
    return {"status": "revoked"}
