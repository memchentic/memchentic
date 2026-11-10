"""Admin hard-delete of directory users + IdP-bound refusal."""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.users import delete_user, ensure_admin_user, register_internal_user
from memchentic.models.entities import Space
from memchentic.services.auth_groups import (
    SLUG_USERS,
    add_member_raw,
    ensure_auth_groups,
    set_idp_bind,
)
from memchentic.services.spaces import get_personal_space

ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "")


def _register(client: TestClient) -> tuple[str, str, str]:
    email = f"del-{uuid.uuid4().hex[:10]}@example.com"
    password = "password-ok-12"
    reg = client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "display_name": "Deletee"},
    )
    assert reg.status_code == 200, reg.text
    user_id = reg.json()["id"]
    token = client.post("/api/auth/login", json={"email": email, "password": password}).json()[
        "access_token"
    ]
    return token, user_id, email


@pytest.mark.skipif(not ADMIN_KEY, reason="ADMIN_API_KEY unset")
def test_admin_deletes_unbound_user(client: TestClient) -> None:
    _token, user_id, _email = _register(client)
    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}

    listed = client.get("/api/users", headers=admin)
    assert listed.status_code == 200, listed.text
    row = next(u for u in listed.json() if u["id"] == user_id)
    assert row["deletable"] is True
    assert row["delete_blocked_reason"] is None

    deleted = client.delete(f"/api/users/{user_id}", headers=admin)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["status"] == "ok"

    missing = client.get(f"/api/users/{user_id}", headers=admin)
    assert missing.status_code == 404


@pytest.mark.skipif(not ADMIN_KEY, reason="ADMIN_API_KEY unset")
def test_non_admin_cannot_delete_user(client: TestClient) -> None:
    _token, target_id, _ = _register(client)
    other_token, _, _ = _register(client)
    refuse = client.delete(
        f"/api/users/{target_id}",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert refuse.status_code == 403
    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}
    assert client.get(f"/api/users/{target_id}", headers=admin).status_code == 200


@pytest.mark.skipif(not ADMIN_KEY, reason="ADMIN_API_KEY unset")
def test_cannot_delete_self(client: TestClient) -> None:
    token, user_id, _ = _register(client)
    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}
    promoted = client.post(
        "/api/auth-groups/administrators/members",
        headers=admin,
        json={"user_id": user_id},
    )
    assert promoted.status_code == 200, promoted.text
    refuse = client.delete(
        f"/api/users/{user_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert refuse.status_code == 403
    assert "own account" in refuse.json()["detail"].lower()


@pytest.mark.skipif(not ADMIN_KEY, reason="ADMIN_API_KEY unset")
def test_cannot_delete_env_admin(client: TestClient) -> None:
    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}
    me = client.get("/api/me", headers=admin).json()
    assert me.get("id")
    refuse = client.delete(f"/api/users/{me['id']}", headers=admin)
    assert refuse.status_code == 403
    assert "admin account" in refuse.json()["detail"].lower()


@pytest.mark.skipif(not ADMIN_KEY, reason="ADMIN_API_KEY unset")
def test_idp_bound_roster_member_not_deletable(client: TestClient) -> None:
    _token, user_id, _email = _register(client)
    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}
    principal = type("P", (), {"is_admin": True})()

    try:
        bind = client.put(
            "/api/auth-groups/users/idp-bind",
            headers=admin,
            json={"idp_group_id": "g-users-del", "idp_group_name": "App Users"},
        )
        assert bind.status_code == 200, bind.text

        listed = client.get("/api/users", headers=admin)
        row = next(u for u in listed.json() if u["id"] == user_id)
        assert row["deletable"] is False
        assert "IdP-bound" in (row.get("delete_blocked_reason") or "")

        refuse = client.delete(f"/api/users/{user_id}", headers=admin)
        assert refuse.status_code == 403
    finally:
        client.put(
            "/api/auth-groups/users/idp-bind",
            headers=admin,
            json={"idp_group_id": None, "idp_group_name": None},
        )
        del principal

    after = client.get(f"/api/users/{user_id}", headers=admin)
    assert after.status_code == 200
    assert after.json()["deletable"] is True

    deleted = client.delete(f"/api/users/{user_id}", headers=admin)
    assert deleted.status_code == 200, deleted.text


@pytest.mark.asyncio
async def test_delete_user_removes_personal_space(session: AsyncSession) -> None:
    user = await register_internal_user(
        session,
        email=f"space-gone-{uuid.uuid4().hex[:8]}@example.com",
        password="password-ok-12",
    )
    personal = await get_personal_space(session, user.id)
    assert personal is not None
    personal_id = personal.id

    admin = await ensure_admin_user(session)
    await ensure_auth_groups(session, admin)
    await delete_user(session, actor_user_id=None, target_user_id=user.id)

    assert await get_personal_space(session, user.id) is None
    left = await session.execute(select(Space).where(Space.id == personal_id))
    assert left.scalar_one_or_none() is None


@pytest.mark.skipif(not ADMIN_KEY, reason="ADMIN_API_KEY unset")
def test_delete_user_reassigns_owned_groups(client: TestClient) -> None:
    """Deleting a group owner reassigns created_by to the acting admin."""
    token, owner_id, _ = _register(client)
    owner_auth = {"Authorization": f"Bearer {token}"}
    group = client.post(
        "/api/spaces",
        headers=owner_auth,
        json={"name": f"reassign-{uuid.uuid4().hex[:8]}"},
    ).json()
    before = client.get(f"/api/spaces/{group['id']}", headers=owner_auth)
    assert before.status_code == 200, before.text
    assert before.json()["created_by"] == owner_id

    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}
    admin_me = client.get("/api/me", headers=admin).json()
    deleted = client.delete(f"/api/users/{owner_id}", headers=admin)
    assert deleted.status_code == 200, deleted.text

    detail = client.get(f"/api/spaces/{group['id']}", headers=admin)
    assert detail.status_code == 200, detail.text
    assert detail.json()["created_by"] == admin_me["id"]


@pytest.mark.asyncio
async def test_delete_user_refuses_when_on_bound_roster(session: AsyncSession) -> None:
    user = await register_internal_user(
        session,
        email=f"bound-{uuid.uuid4().hex[:8]}@example.com",
        password="password-ok-12",
    )
    admin = await ensure_admin_user(session)
    groups = await ensure_auth_groups(session, admin)
    principal = type("P", (), {"is_admin": True})()
    try:
        await set_idp_bind(
            session,
            principal,
            SLUG_USERS,
            idp_group_id="g1",
            idp_group_name="G1",
        )
        await add_member_raw(session, groups[SLUG_USERS], user.id, source="idp")
        await session.commit()

        with pytest.raises(PermissionError, match="IdP-bound"):
            await delete_user(session, actor_user_id=None, target_user_id=user.id)
    finally:
        await set_idp_bind(
            session,
            principal,
            SLUG_USERS,
            idp_group_id=None,
            idp_group_name=None,
        )
