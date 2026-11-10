"""API keys: binding to the owning user, the admin key, cycle and issue rules."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from support import RegisteredUser, call_tool, register_user

ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "")


def _tool(client: TestClient, token: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call an MCP tool over streamable HTTP.

    Args:
        client: In-process test client.
        token: Bearer token or API key.
        name: Tool name.
        args: Tool arguments.

    Returns:
        The JSON-RPC ``result`` object.
    """
    return call_tool(client, {"Authorization": f"Bearer {token}"}, name, args)


def _user(client: TestClient) -> tuple[str, str, str]:
    """Register and log in a fresh internal user.

    Args:
        client: In-process test client.

    Returns:
        Tuple of (access token, user id, email).
    """
    user = register_user(client)
    return user.token, user.user_id, user.email


def test_admin_key_writes_as_admin_user(client: TestClient) -> None:
    """The env admin key is a real user: personal writes work and stamp created_by."""
    auth = {"Authorization": f"Bearer {ADMIN_KEY}"}
    me = client.get("/api/me", headers=auth).json()
    assert me["is_admin"] is True
    assert me["id"]
    assert me.get("personal_space_id")
    body = uuid.uuid4().hex
    resp = client.post(
        "/api/memories",
        headers=auth,
        json={"title": f"admin-owner-{body}", "body": body, "force": True},
    )
    assert resp.status_code == 200, resp.text
    row = resp.json()
    assert row["created_by"] == me["id"]
    assert row.get("created_by_display_name") or row.get("created_by_email")


def test_api_keys_bind_to_user_and_admin_key_is_admin(client: TestClient) -> None:
    """A per-user key acts as that user; the env admin key acts as admin."""
    token, _, email = _user(client)
    raw = client.post(
        "/api/api-keys", headers={"Authorization": f"Bearer {token}"}, json={"name": "test"}
    ).json()["key"]
    assert _tool(client, raw, "me", {})["structuredContent"]["email"] == email
    admin_me = _tool(client, ADMIN_KEY, "me", {})["structuredContent"]
    assert admin_me["is_admin"] is True
    assert admin_me.get("personal_space_id")


def test_api_key_cycle_is_own_only_and_admin_can_cycle_others(client: TestClient) -> None:
    """Users list/cycle only themselves; admins can cycle another account."""
    token_a, user_a, _ = _user(client)
    token_b, user_b, _ = _user(client)
    auth_a = {"Authorization": f"Bearer {token_a}"}
    auth_b = {"Authorization": f"Bearer {token_b}"}

    first = client.post("/api/api-keys/cycle", headers=auth_a, json={}).json()
    assert first["key"].startswith("mc_")
    listed = client.get("/api/api-keys", headers=auth_a).json()
    assert len(listed) == 1
    assert listed[0]["user_id"] == user_a
    assert all(row["user_id"] == user_a for row in listed)

    second = client.post("/api/api-keys/cycle", headers=auth_a, json={}).json()
    assert second["key"] != first["key"]
    assert client.get("/api/api-keys", headers=auth_a).json()[0]["prefix"] == second["prefix"]
    # Prior secret no longer authenticates.
    denied = client.get("/api/me", headers={"Authorization": f"Bearer {first['key']}"})
    assert denied.status_code in {401, 403}

    assert (
        client.post("/api/api-keys/cycle", headers=auth_b, json={"user_id": user_a}).status_code
        == 403
    )

    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}
    admin_list = client.get("/api/api-keys", headers=admin).json()
    assert user_a in {row["user_id"] for row in admin_list}
    assert user_b not in {row["user_id"] for row in admin_list}
    cycled = client.post("/api/api-keys/cycle", headers=admin, json={"user_id": user_b}).json()
    assert cycled["user_id"] == user_b
    assert client.get("/api/api-keys", headers=auth_b).json()[0]["prefix"] == cycled["prefix"]
    assert user_b in {row["user_id"] for row in client.get("/api/api-keys", headers=admin).json()}


def test_non_admin_cannot_issue_keys_for_another_user(client: TestClient) -> None:
    """A user-supplied user_id on key issue is refused for non-admins."""
    token_a, _, _ = _user(client)
    _, user_b, _ = _user(client)
    issued = client.post(
        "/api/api-keys",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"name": "steal", "user_id": user_b},
    )
    assert issued.status_code == 403


def test_revoked_key_stops_authenticating(
    client: TestClient, make_user: Callable[..., RegisteredUser]
) -> None:
    user = make_user()
    created = client.post("/api/api-keys", headers=user.headers, json={"name": "revoke-me"})
    assert created.status_code == 200, created.text
    key_id = created.json()["id"]
    key_auth = {"Authorization": f"Bearer {created.json()['key']}"}
    assert client.get("/api/me", headers=key_auth).status_code == 200

    revoked = client.delete(f"/api/api-keys/{key_id}", headers=user.headers)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json() == {"status": "revoked"}

    assert client.get("/api/me", headers=key_auth).status_code == 401
    listed = client.get("/api/api-keys", headers=user.headers).json()
    assert key_id not in {row["id"] for row in listed}


def test_user_cannot_revoke_another_users_key(
    client: TestClient, make_user: Callable[..., RegisteredUser]
) -> None:
    owner = make_user()
    other = make_user()
    created = client.post("/api/api-keys", headers=owner.headers, json={"name": "keep"}).json()

    denied = client.delete(f"/api/api-keys/{created['id']}", headers=other.headers)
    assert denied.status_code == 404

    key_auth = {"Authorization": f"Bearer {created['key']}"}
    assert client.get("/api/me", headers=key_auth).status_code == 200
