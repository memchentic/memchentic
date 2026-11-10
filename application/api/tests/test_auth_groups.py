"""Fixed Auth Groups (Administrators / Users) + IdP binds."""

from __future__ import annotations

import logging
import os
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth import idp_groups
from memchentic.auth.idp_groups import groups_from_claims
from memchentic.auth.users import ensure_admin_user, register_internal_user
from memchentic.db.session import set_rls_context
from memchentic.models.entities import ApiKey
from memchentic.services.auth_groups import (
    SLUG_ADMINISTRATORS,
    SLUG_USERS,
    add_member,
    ensure_auth_groups,
    list_members,
    remove_member,
    set_idp_bind,
    sync_user_from_idp_groups,
    user_is_administrator,
)
from memchentic.services.errors import ForbiddenError
from memchentic.settings import Settings, get_settings

ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "")


def _user(client: TestClient) -> tuple[str, str, str]:
    email = f"u-{uuid.uuid4().hex[:10]}@example.com"
    password = "password-ok-12"
    reg = client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "display_name": "Tester"},
    )
    assert reg.status_code == 200, reg.text
    token = client.post("/api/auth/login", json={"email": email, "password": password}).json()[
        "access_token"
    ]
    me = client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).json()
    return token, me["id"], email


def test_groups_from_claims_extracts_ids_and_names() -> None:
    got = groups_from_claims(
        {
            "groups": ["okta-admins", {"id": "00g1", "name": "Everyone"}],
            "realm_access": {"roles": ["realm-role"]},
        }
    )
    assert "okta-admins" in got
    assert "00g1" in got
    assert "Everyone" in got
    assert "realm-role" in got


@pytest.mark.asyncio
async def test_ensure_auth_groups_exclusive_and_locks_admin(session: AsyncSession) -> None:
    settings = get_settings()
    admin = await ensure_admin_user(session, settings)
    groups = await ensure_auth_groups(session, admin)
    assert SLUG_ADMINISTRATORS in groups
    assert SLUG_USERS in groups
    assert await user_is_administrator(session, admin.id)

    admin_members = await list_members(
        session, type("P", (), {"is_admin": True})(), SLUG_ADMINISTRATORS
    )
    assert any(m["user_id"] == str(admin.id) and m["locked"] for m in admin_members)

    users_members = await list_members(session, type("P", (), {"is_admin": True})(), SLUG_USERS)
    assert all(m["user_id"] != str(admin.id) for m in users_members)

    user = await register_internal_user(
        session,
        email=f"member-{uuid.uuid4().hex[:8]}@example.com",
        password="password-ok-12",
    )
    await ensure_auth_groups(session, admin)
    users_members = await list_members(session, type("P", (), {"is_admin": True})(), SLUG_USERS)
    assert any(m["user_id"] == str(user.id) for m in users_members)

    with pytest.raises(PermissionError, match="Cannot remove"):
        await remove_member(
            session,
            type("P", (), {"is_admin": True})(),
            SLUG_ADMINISTRATORS,
            admin.id,
        )


@pytest.mark.asyncio
async def test_idp_sync_administrators_wins(session: AsyncSession) -> None:
    settings = get_settings()
    admin = await ensure_admin_user(session, settings)
    await ensure_auth_groups(session, admin)
    principal = type("P", (), {"is_admin": True})()
    await set_idp_bind(
        session,
        principal,
        SLUG_ADMINISTRATORS,
        idp_group_id="admins-id",
        idp_group_name="Admins",
    )
    await set_idp_bind(
        session,
        principal,
        SLUG_USERS,
        idp_group_id="users-id",
        idp_group_name="Users",
    )
    user = await register_internal_user(
        session,
        email=f"sync-{uuid.uuid4().hex[:8]}@example.com",
        password="password-ok-12",
    )
    await sync_user_from_idp_groups(session, user, {"admins-id", "users-id"}, settings=settings)
    await session.commit()
    assert await user_is_administrator(session, user.id)

    await sync_user_from_idp_groups(session, user, {"users-id"}, settings=settings)
    await session.commit()
    assert not await user_is_administrator(session, user.id)

    # Clear binds for other tests sharing the DB.
    await set_idp_bind(
        session, principal, SLUG_ADMINISTRATORS, idp_group_id=None, idp_group_name=None
    )
    await set_idp_bind(session, principal, SLUG_USERS, idp_group_id=None, idp_group_name=None)


def test_auth_groups_api_bind_and_refuse_edits(client: TestClient) -> None:
    if not ADMIN_KEY:
        pytest.skip("ADMIN_API_KEY not set")
    token, user_id, _email = _user(client)
    user_auth = {"Authorization": f"Bearer {token}"}
    admin_auth = {"Authorization": f"Bearer {ADMIN_KEY}"}

    assert client.get("/api/auth-groups", headers=user_auth).status_code == 403

    groups = client.get("/api/auth-groups", headers=admin_auth)
    assert groups.status_code == 200, groups.text
    slugs = {g["slug"] for g in groups.json()}
    assert slugs == {"administrators", "users"}

    catalog = client.get("/api/auth-groups/idp-groups", headers=admin_auth)
    assert catalog.status_code == 200, catalog.text
    body = catalog.json()
    assert "supports_groups" in body
    assert "groups" in body

    bind = client.put(
        "/api/auth-groups/users/idp-bind",
        headers=admin_auth,
        json={"idp_group_id": "g-users", "idp_group_name": "App Users"},
    )
    assert bind.status_code == 200, bind.text
    assert bind.json()["roster_bound"] is True

    refuse_add = client.post(
        "/api/auth-groups/users/members",
        headers=admin_auth,
        json={"user_id": user_id},
    )
    assert refuse_add.status_code == 403

    refuse_del = client.delete(
        f"/api/auth-groups/users/members/{user_id}",
        headers=admin_auth,
    )
    assert refuse_del.status_code == 403

    clear = client.put(
        "/api/auth-groups/users/idp-bind",
        headers=admin_auth,
        json={"idp_group_id": None, "idp_group_name": None},
    )
    assert clear.status_code == 200, clear.text
    assert clear.json()["roster_bound"] is False

    add = client.post(
        "/api/auth-groups/administrators/members",
        headers=admin_auth,
        json={"user_id": user_id},
    )
    assert add.status_code == 200, add.text
    me = client.get("/api/me", headers=user_auth).json()
    assert me["is_admin"] is True

    remove = client.delete(
        f"/api/auth-groups/administrators/members/{user_id}",
        headers=admin_auth,
    )
    assert remove.status_code == 200, remove.text

    admin_me = client.get("/api/me", headers=admin_auth).json()
    locked = client.delete(
        f"/api/auth-groups/administrators/members/{admin_me['id']}",
        headers=admin_auth,
    )
    assert locked.status_code == 403


def test_admin_key_request_cost_does_not_depend_on_user_count(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The admin key is resolved with a lookup, not a re-hash and a roster rebuild."""
    from sqlalchemy import event

    import memchentic.auth.users as users_module
    from memchentic.db.session import get_engine

    admin_auth = {"Authorization": f"Bearer {ADMIN_KEY}"}
    counters = {"bcrypt": 0, "sql": 0}
    real_verify = users_module.verify_password

    def counting_verify(password: str, password_hash: str) -> bool:
        counters["bcrypt"] += 1
        return real_verify(password, password_hash)

    def before_execute(*_args: object) -> None:
        counters["sql"] += 1

    def measure() -> tuple[int, int]:
        counters["bcrypt"] = counters["sql"] = 0
        assert client.get("/api/me", headers=admin_auth).status_code == 200
        return counters["bcrypt"], counters["sql"]

    monkeypatch.setattr(users_module, "verify_password", counting_verify)
    engine = get_engine().sync_engine
    event.listen(engine, "before_cursor_execute", before_execute)
    try:
        first_bcrypt, first_sql = measure()
        for _ in range(5):
            _user(client)
        second_bcrypt, second_sql = measure()
    finally:
        event.remove(engine, "before_cursor_execute", before_execute)

    assert first_bcrypt == 0
    assert second_bcrypt == 0
    assert second_sql <= first_sql
    assert first_sql < 40


def test_unbinding_users_backfills_the_roster(client: TestClient) -> None:
    """Users created while the roster was IdP-bound are placed when the bind is cleared."""
    admin_auth = {"Authorization": f"Bearer {ADMIN_KEY}"}
    bind = client.put(
        "/api/auth-groups/users/idp-bind",
        headers=admin_auth,
        json={"idp_group_id": "g-users-backfill", "idp_group_name": "App Users"},
    )
    assert bind.status_code == 200, bind.text
    try:
        _token, user_id, _email = _user(client)
    finally:
        clear = client.put(
            "/api/auth-groups/users/idp-bind",
            headers=admin_auth,
            json={"idp_group_id": None, "idp_group_name": None},
        )
    assert clear.status_code == 200, clear.text
    members = client.get("/api/auth-groups/users/members", headers=admin_auth).json()
    assert user_id in {m["user_id"] for m in members}


def test_login_does_not_rebuild_rosters(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user password login neither runs the roster rebuild nor a second verify."""
    import memchentic.services.auth_groups as auth_groups_module

    email = f"u-{uuid.uuid4().hex[:10]}@example.com"
    password = "password-ok-12"
    reg = client.post("/api/auth/register", json={"email": email, "password": password})
    assert reg.status_code == 200, reg.text

    async def fail(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("ensure_auth_groups ran on a request path")

    monkeypatch.setattr(auth_groups_module, "ensure_auth_groups", fail)
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    groups = client.get("/api/auth-groups", headers={"Authorization": f"Bearer {ADMIN_KEY}"})
    assert groups.status_code == 200, groups.text


async def test_admin_email_change_demotes_previous_env_admin(session: AsyncSession) -> None:
    """Pointing ADMIN_EMAIL at another account must not leave two break-glass admins."""
    settings = get_settings()
    admin_principal = type("P", (), {"is_admin": True})()
    old_admin = await ensure_admin_user(session, settings)
    await ensure_auth_groups(session, old_admin)

    email = f"next-admin-{uuid.uuid4().hex[:8]}@example.com"
    successor = await register_internal_user(session, email=email, password="password-ok-12")
    await set_rls_context(session, user_id=None, is_admin=True)
    key = ApiKey(
        id=uuid.uuid4(),
        user_id=successor.id,
        name="before-promotion",
        key_prefix="mc_test",
        key_hash=uuid.uuid4().hex,
    )
    session.add(key)
    await session.commit()

    changed = SimpleNamespace(
        admin_contact_email=email,
        admin_display_name="Next Admin",
        admin_password="another-password-12",
    )
    try:
        await ensure_auth_groups(session, await ensure_admin_user(session, changed))
        await set_rls_context(session, user_id=None, is_admin=True)

        assert await user_is_administrator(session, successor.id)
        assert not await user_is_administrator(session, old_admin.id)
        users_members = await list_members(session, admin_principal, SLUG_USERS)
        assert any(m["user_id"] == str(old_admin.id) for m in users_members)
        revoked_at = await session.scalar(select(ApiKey.revoked_at).where(ApiKey.id == key.id))
        assert revoked_at is not None
    finally:
        # Swap back so later tests see the configured break-glass admin.
        await ensure_auth_groups(session, await ensure_admin_user(session, settings))
        await set_rls_context(session, user_id=None, is_admin=True)

    assert await user_is_administrator(session, old_admin.id)
    assert not await user_is_administrator(session, successor.id)


async def test_unchanged_admin_email_revokes_nothing(session: AsyncSession) -> None:
    """Re-running ensure_auth_groups for the same admin keeps its credentials."""
    admin = await ensure_admin_user(session, get_settings())
    await ensure_auth_groups(session, admin)
    await set_rls_context(session, user_id=None, is_admin=True)
    key = ApiKey(
        id=uuid.uuid4(),
        user_id=admin.id,
        name="kept",
        key_prefix="mc_test",
        key_hash=uuid.uuid4().hex,
    )
    session.add(key)
    await session.commit()

    await ensure_auth_groups(session, await ensure_admin_user(session, get_settings()))
    await set_rls_context(session, user_id=None, is_admin=True)

    revoked_at = await session.scalar(select(ApiKey.revoked_at).where(ApiKey.id == key.id))
    assert revoked_at is None
    assert await user_is_administrator(session, admin.id)


def test_add_to_users_allowed_when_admins_bound_and_target_not_admin(
    client: TestClient,
) -> None:
    """A bound Administrators roster must not freeze the manual Users roster."""
    _token, user_id, _email = _user(client)
    admin_auth = {"Authorization": f"Bearer {ADMIN_KEY}"}
    bind = client.put(
        "/api/auth-groups/administrators/idp-bind",
        headers=admin_auth,
        json={"idp_group_id": "grp-admins", "idp_group_name": "Admins"},
    )
    assert bind.status_code == 200, bind.text
    try:
        removed = client.delete(f"/api/auth-groups/users/members/{user_id}", headers=admin_auth)
        assert removed.status_code == 200, removed.text
        added = client.post(
            "/api/auth-groups/users/members", headers=admin_auth, json={"user_id": user_id}
        )
        assert added.status_code == 200, added.text
        members = client.get("/api/auth-groups/users/members", headers=admin_auth).json()
        assert user_id in {m["user_id"] for m in members}
    finally:
        client.put(
            "/api/auth-groups/administrators/idp-bind",
            headers=admin_auth,
            json={"idp_group_id": None, "idp_group_name": None},
        )


async def test_add_to_users_refused_for_member_of_bound_administrators(
    session: AsyncSession,
) -> None:
    """An IdP-managed administrator cannot be moved to Users by a manual edit."""
    settings = get_settings()
    admin = await ensure_admin_user(session, settings)
    await ensure_auth_groups(session, admin)
    principal = type("P", (), {"is_admin": True})()
    await set_idp_bind(
        session, principal, SLUG_ADMINISTRATORS, idp_group_id="admins-x", idp_group_name="Ax"
    )
    user = await register_internal_user(
        session,
        email=f"idp-admin-{uuid.uuid4().hex[:8]}@example.com",
        password="password-ok-12",
    )
    try:
        await sync_user_from_idp_groups(session, user, {"admins-x"}, settings=settings)
        assert await user_is_administrator(session, user.id)
        with pytest.raises(ForbiddenError, match="IdP-bound Administrators"):
            await add_member(session, principal, SLUG_USERS, user.id)
        assert await user_is_administrator(session, user.id)
    finally:
        await set_idp_bind(
            session, principal, SLUG_ADMINISTRATORS, idp_group_id=None, idp_group_name=None
        )


def _okta_env(monkeypatch: pytest.MonkeyPatch, *, issuer: str) -> None:
    monkeypatch.setenv("OAUTH_PROVIDER", "okta")
    monkeypatch.setenv("OAUTH_CLIENT_ID", "client-id")
    monkeypatch.setenv("OAUTH_CLIENT_SECRET", "not-a-real-secret")
    monkeypatch.setenv("OAUTH_ISSUER", issuer)


async def test_list_idp_groups_hides_upstream_error_detail(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An IdP failure is logged and reaches the UI as a fixed message."""
    _okta_env(monkeypatch, issuer="https://example.okta.com")

    async def _boom(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("upstream detail 4f2a")

    monkeypatch.setattr(idp_groups, "_client_credentials_token", _boom)
    with caplog.at_level(logging.WARNING, logger="memchentic.auth.idp_groups"):
        result = await idp_groups.list_idp_groups(Settings())
    assert result.groups == []
    assert result.error == "Failed to list IdP groups; see the server log"
    assert "IdP group listing failed" in caplog.text


async def test_list_idp_groups_keeps_configuration_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configuration problem found by this module is still shown as-is."""
    _okta_env(monkeypatch, issuer="not-a-url")
    result = await idp_groups.list_idp_groups(Settings())
    assert result.error == "OAUTH_ISSUER is required for Okta group listing"
