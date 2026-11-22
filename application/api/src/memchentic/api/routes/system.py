"""Admin system settings and auth-group administration."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.api.schemas import (
    AuthGroupIdpBindIn,
    AuthGroupMemberIn,
    AutomationSettingIn,
    MailSettingIn,
    RegistrationSettingIn,
    WebhookSettingIn,
)
from memchentic.auth.idp_groups import list_idp_groups
from memchentic.auth.principal import Principal
from memchentic.deps import db_session, get_principal
from memchentic.services import auth_groups as auth_group_service
from memchentic.services import instance_settings as instance_settings_service
from memchentic.services.mail import send_test_email
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


@router.get("/system/automation")
async def get_automation_setting(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, bool]:
    """Admin: hygiene Automation toggles (default off)."""
    if not principal.is_admin:
        raise HTTPException(403, "Administrators only")
    return await instance_settings_service.get_automation_settings(session)


@router.put("/system/automation")
async def put_automation_setting(
    body: AutomationSettingIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, bool]:
    """Admin: enable or disable hygiene Automation passes."""
    return await instance_settings_service.set_automation_settings(
        session,
        principal,
        resolve_supersedes=body.resolve_supersedes,
        resolve_near_dups=body.resolve_near_dups,
        propose_consolidations=body.propose_consolidations,
        auto_apply_consolidations=body.auto_apply_consolidations,
    )


@router.post("/system/automation/run")
async def run_automation_now(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, int]:
    """Admin: run one hygiene Automation pass immediately.

    Applies whatever toggles are currently enabled. Safe when both are off
    (returns zero counts).
    """
    if not principal.is_admin:
        raise HTTPException(403, "Administrators only")
    from memchentic.services.memory_hygiene import run_hygiene_automation

    return await run_hygiene_automation(session)


@router.get("/system/webhooks")
async def get_webhook_setting(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    """Admin: outbound webhook settings (secret never returned)."""
    if not principal.is_admin:
        raise HTTPException(403, "Administrators only")
    return await instance_settings_service.webhook_settings_public(session)


@router.put("/system/webhooks")
async def put_webhook_setting(
    body: WebhookSettingIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    """Admin: enable or configure mutation webhooks."""
    secret = body.secret
    if secret == "" and not body.clear_secret:
        secret = None
    return await instance_settings_service.set_webhook_settings(
        session,
        principal,
        enabled=body.enabled,
        url=body.url,
        secret=secret,
        clear_secret=body.clear_secret,
    )


@router.get("/system/mail")
async def get_mail_setting(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    """Admin: outbound SMTP settings (password never returned)."""
    if not principal.is_admin:
        raise HTTPException(403, "Administrators only")
    return await instance_settings_service.mail_settings_public(session)


@router.put("/system/mail")
async def put_mail_setting(
    body: MailSettingIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    """Admin: update outbound SMTP settings."""
    password = body.password
    if password == "" and not body.clear_password:
        password = None
    return await instance_settings_service.set_mail_settings(
        session,
        principal,
        host=body.host,
        port=body.port,
        username=body.username,
        password=password,
        clear_password=body.clear_password,
        from_address=body.from_address,
        use_tls=body.use_tls,
    )


@router.post("/system/mail/test")
async def send_mail_test(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, str]:
    """Admin: send a test message to the caller's email address."""
    if not principal.is_admin:
        raise HTTPException(403, "Administrators only")
    if not principal.email:
        raise HTTPException(400, "Administrator has no email address")
    try:
        await send_test_email(session, to=principal.email)
    except Exception as exc:
        raise HTTPException(400, f"Mail test failed: {exc}") from exc
    return {"status": "sent", "to": principal.email}


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
