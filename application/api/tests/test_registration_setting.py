"""Public registration gate: REGISTER_ENABLED + System Settings."""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.users import get_user_by_email
from memchentic.db.session import get_session_factory
from memchentic.services.auth_groups import set_idp_bind
from memchentic.services.auth_roster import SLUG_ADMINISTRATORS, SLUG_USERS, ensure_auth_groups
from memchentic.services.instance_settings import ensure_instance_settings, set_register_enabled
from memchentic.services.users import ensure_admin_user, upsert_oauth_user

ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "")


def _admin_auth() -> dict[str, str]:
    if not ADMIN_KEY:
        pytest.skip("ADMIN_API_KEY not set")
    return {"Authorization": f"Bearer {ADMIN_KEY}"}


def test_provider_reports_register_enabled(client: TestClient) -> None:
    info = client.get("/api/provider").json()
    assert "register_enabled" in info
    assert isinstance(info["register_enabled"], bool)


def test_registration_setting_admin_only_and_gates_public_register(
    client: TestClient,
) -> None:
    admin = _admin_auth()
    email = f"reg-{uuid.uuid4().hex[:10]}@example.com"
    password = "password-ok-12"

    denied = client.get("/api/system/registration")
    assert denied.status_code == 401

    current = client.get("/api/system/registration", headers=admin)
    assert current.status_code == 200, current.text
    assert current.json()["enabled"] is True

    off = client.put("/api/system/registration", headers=admin, json={"enabled": False})
    assert off.status_code == 200, off.text
    assert off.json()["enabled"] is False
    assert client.get("/api/provider").json()["register_enabled"] is False

    blocked = client.post(
        "/api/auth/register",
        json={"email": email, "password": password},
    )
    assert blocked.status_code == 403, blocked.text

    # Admins can still create users while public registration is off.
    created = client.post(
        "/api/auth/register",
        headers=admin,
        json={"email": email, "password": password, "display_name": "Admin-made"},
    )
    assert created.status_code == 200, created.text

    on = client.put("/api/system/registration", headers=admin, json={"enabled": True})
    assert on.status_code == 200, on.text
    assert on.json()["enabled"] is True

    other = f"reg2-{uuid.uuid4().hex[:10]}@example.com"
    ok = client.post(
        "/api/auth/register",
        json={"email": other, "password": password},
    )
    assert ok.status_code == 200, ok.text


def test_boot_keeps_registration_toggle_set_in_system_settings(client: TestClient) -> None:
    """A restart does not re-open registration that an administrator closed."""
    admin = _admin_auth()

    async def _boot_seed() -> None:
        # What every process start does (``lifespan`` in ``memchentic.main``).
        async with get_session_factory()() as session:
            await ensure_instance_settings(session)

    off = client.put("/api/system/registration", headers=admin, json={"enabled": False})
    assert off.status_code == 200, off.text
    try:
        assert client.portal is not None
        client.portal.call(_boot_seed)  # run on the application's event loop
        assert client.get("/api/provider").json()["register_enabled"] is False
        current = client.get("/api/system/registration", headers=admin)
        assert current.json()["enabled"] is False
    finally:
        # Other tests register users: always re-open registration.
        on = client.put("/api/system/registration", headers=admin, json={"enabled": True})
        assert on.status_code == 200, on.text


_ADMIN = type("P", (), {"is_admin": True})()


async def _unbind_auth_groups(session: AsyncSession) -> None:
    for slug in (SLUG_ADMINISTRATORS, SLUG_USERS):
        await set_idp_bind(session, _ADMIN, slug, idp_group_id=None, idp_group_name=None)


async def test_idp_provisioning_respects_registration_toggle(session: AsyncSession) -> None:
    """With no auth group bind, the registration toggle gates first-time IdP sign-in."""
    await ensure_auth_groups(session, await ensure_admin_user(session))
    await _unbind_auth_groups(session)
    suffix = uuid.uuid4().hex[:10]
    email = f"closed-{suffix}@example.com"
    await set_register_enabled(session, _ADMIN, False)
    try:
        with pytest.raises(ValueError, match="Sign-up is disabled"):
            await upsert_oauth_user(session, provider="okta", subject=f"okta-{suffix}", email=email)
        assert await get_user_by_email(session, email) is None
    finally:
        await set_register_enabled(session, _ADMIN, True)
    created = await upsert_oauth_user(
        session, provider="okta", subject=f"okta-{suffix}", email=email
    )
    assert created.email == email


async def test_idp_provisioning_follows_users_auth_group_bind(session: AsyncSession) -> None:
    """A bound Users roster admits only members of the bound IdP group."""
    await ensure_auth_groups(session, await ensure_admin_user(session))
    await _unbind_auth_groups(session)
    suffix = uuid.uuid4().hex[:10]
    bind = f"idp-users-{suffix}"
    await set_idp_bind(session, _ADMIN, SLUG_USERS, idp_group_id=bind, idp_group_name=None)
    try:
        # Registration is enabled here, but the bound roster decides.
        with pytest.raises(ValueError, match="Sign-up is disabled"):
            await upsert_oauth_user(
                session,
                provider="okta",
                subject=f"out-{suffix}",
                email=f"out-{suffix}@example.com",
                idp_groups=["some-other-group"],
            )
        await set_register_enabled(session, _ADMIN, False)
        member = await upsert_oauth_user(
            session,
            provider="okta",
            subject=f"in-{suffix}",
            email=f"in-{suffix}@example.com",
            idp_groups=[bind],
        )
        assert member.email == f"in-{suffix}@example.com"
    finally:
        await set_register_enabled(session, _ADMIN, True)
        await _unbind_auth_groups(session)
