"""Admin system settings and auth-group administration."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.api.schemas import AuthGroupIdpBindIn, AuthGroupMemberIn, RegistrationSettingIn
from memchentic.auth.idp_groups import list_idp_groups
from memchentic.auth.principal import Principal
from memchentic.deps import db_session, get_principal
from memchentic.services import auth_groups as auth_group_service
from memchentic.services import instance_settings as instance_settings_service
from memchentic.services.validation import require_uuid
from memchentic.settings import get_settings

router = APIRouter()


@router.get("/system/registration")
async def get_registration_setting(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, bool]:
    """Admin: whether public self-registration is enabled."""
    if not principal.is_admin:
        raise HTTPException(403, "Administrators only")
    return {"enabled": await instance_settings_service.get_register_enabled(session)}


@router.put("/system/registration")
async def put_registration_setting(
    body: RegistrationSettingIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, bool]:
    """Admin: enable or disable public self-registration."""
    enabled = await instance_settings_service.set_register_enabled(session, principal, body.enabled)
    return {"enabled": enabled}


@router.get("/auth-groups")
async def list_auth_groups(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> list[dict[str, Any]]:
    """List auth groups. Administrators only (403 otherwise)."""
    return [dict(row) for row in await auth_group_service.list_groups(session, principal)]


@router.get("/auth-groups/idp-groups")
async def list_auth_idp_groups(
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Admin: list IdP directory groups for the Bound IdP group dropdown."""
    if not principal.is_admin:
        raise HTTPException(403, "Administrators only")
    result = await list_idp_groups(get_settings())
    return {
        "provider": result.provider,
        "supports_groups": result.supports_groups,
        "groups": [{"id": g.id, "name": g.name} for g in result.groups],
        "error": result.error,
    }


@router.get("/auth-groups/{slug}/idp-bind")
async def get_auth_group_idp_bind(
    slug: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    """Return the IdP group bound to an auth group.

    Administrators only. Errors: 403, 404 when the group does not exist.
    """
    return dict(await auth_group_service.get_idp_bind(session, principal, slug))


@router.put("/auth-groups/{slug}/idp-bind")
async def put_auth_group_idp_bind(
    slug: str,
    body: AuthGroupIdpBindIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    """Bind an auth group to an IdP group, or clear the bind.

    Administrators only. Errors: 403, 404 when the group does not exist.
    """
    return dict(
        await auth_group_service.set_idp_bind(
            session,
            principal,
            slug,
            idp_group_id=body.idp_group_id,
            idp_group_name=body.idp_group_name,
        )
    )


@router.get("/auth-groups/{slug}/members")
async def list_auth_group_members(
    slug: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> list[dict[str, Any]]:
    """List the members of an auth group.

    Administrators only. Errors: 403, 404 when the group does not exist.
    """
    return [dict(row) for row in await auth_group_service.list_members(session, principal, slug)]


@router.post("/auth-groups/{slug}/members")
async def add_auth_group_member(
    slug: str,
    body: AuthGroupMemberIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    """Add a user to an auth group.

    Administrators only. Errors: 403 (also for IdP-bound rosters), 404.
    """
    return dict(
        await auth_group_service.add_member(
            session, principal, slug, require_uuid(body.user_id, "user_id")
        )
    )


@router.delete("/auth-groups/{slug}/members/{user_id}")
async def remove_auth_group_member(
    slug: str,
    user_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, str]:
    """Remove a user from an auth group.

    Administrators only. Errors: 403 (also for IdP-bound rosters), 404.
    """
    await auth_group_service.remove_member(session, principal, slug, user_id)
    return {"status": "removed"}
