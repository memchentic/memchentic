"""Current-user profile and the user directory."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.api.schemas import MeIn
from memchentic.auth.principal import Principal
from memchentic.auth.tokens import ACCESS_TOKEN_SECONDS
from memchentic.auth.users import get_user, providers_for
from memchentic.deps import db_session, get_principal
from memchentic.models.entities import User
from memchentic.services import auth_groups as auth_group_service
from memchentic.services import limits
from memchentic.services import spaces as space_service
from memchentic.services import write_target as write_target_service
from memchentic.services.users import (
    delete_user,
    directory_user_visible,
    list_directory_users,
    lookup_user_by_email,
    revoke_user_credentials,
    user_delete_eligibility,
)
from memchentic.services.validation import parse_uuid

router = APIRouter()


async def _admin_user_fields(
    session: AsyncSession,
    principal: Principal,
    user: User,
    *,
    bound_user_ids: set[uuid.UUID] | None = None,
) -> dict[str, Any]:
    """Build the fields that only administrators see for a directory user."""
    deletable, reason = await user_delete_eligibility(
        session,
        actor_user_id=principal.user_id,
        target=user,
        bound_user_ids=bound_user_ids,
    )
    return {
        "providers": providers_for(user),
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "deletable": deletable,
        "delete_blocked_reason": reason,
    }


@router.get("/me")
async def me(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    """Return the caller's identity, spaces and pending invites. Authenticated callers."""
    return await space_service.me_payload(session, principal, invite_details=True)


@router.patch("/me")
async def patch_me(
    body: MeIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    """Set the caller's default shared group. Authenticated callers.

    Errors: 4xx when the group id is invalid or cannot be used.
    """
    await write_target_service.set_default_shared_space(
        session, principal, parse_uuid(body.default_shared_space_id, "default_shared_space_id")
    )
    return await space_service.me_payload(session, principal, invite_details=True)


@router.get("/users")
async def users(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> list[dict]:
    """Authenticated directory for group invites; admins get extra metadata.

    Non-admins only see users who share at least one group space with them.
    """
    rows = await list_directory_users(session, principal)
    bound_user_ids: set[uuid.UUID] | None = None
    if principal.is_admin:
        bound_user_ids = await auth_group_service.idp_bound_user_ids(session)
    out: list[dict] = []
    for u in rows:
        row: dict = {
            "id": str(u.id),
            "email": u.email,
            "display_name": u.display_name,
        }
        if principal.is_admin:
            row.update(
                await _admin_user_fields(session, principal, u, bound_user_ids=bound_user_ids)
            )
        out.append(row)
    return out


@router.get("/users/lookup")
async def users_lookup(
    email: str = Query(..., min_length=1, max_length=limits.EMAIL_MAX),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Exact email lookup for admins.

    Misses and non-admin callers both receive 404 to avoid enumeration.
    """
    user = await lookup_user_by_email(session, principal, email)
    if user is None:
        raise HTTPException(404, "Not found")
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
    }


@router.get("/users/{user_id}")
async def show_user(
    user_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Directory detail plus groups the caller is allowed to know about."""
    if not await directory_user_visible(session, principal, user_id):
        raise HTTPException(404, "Not found")
    user = await get_user(session, user_id)
    if not user:
        raise HTTPException(404, "Not found")
    groups = await space_service.list_visible_groups_for_user(session, principal, user_id)
    out: dict[str, Any] = {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "groups": [space_service.space_to_dict(s) for s in groups],
    }
    if principal.is_admin:
        out.update(await _admin_user_fields(session, principal, user))
    return out


@router.delete("/users/{user_id}")
async def remove_user(
    user_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, str]:
    """Admin hard-delete of a directory user (IdP-bound roster members refused)."""
    if not principal.is_admin:
        raise HTTPException(403, "Admin required")
    await delete_user(session, actor_user_id=principal.user_id, target_user_id=user_id)
    return {"status": "ok"}


@router.post("/users/{user_id}/revoke-credentials")
async def revoke_user_credentials_route(
    user_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    """Admin: end a user's sessions, refresh tokens and API keys; keep the account."""
    if not principal.is_admin:
        raise HTTPException(403, "Admin required")
    await revoke_user_credentials(session, principal, user_id)
    return {
        "status": "ok",
        "access_tokens_expire_within_seconds": ACCESS_TOKEN_SECONDS,
    }
