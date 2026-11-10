"""Scoped user directory and email lookup for roster managers."""

from __future__ import annotations

import os
import uuid

from fastapi.testclient import TestClient

ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "")


def _user(client: TestClient) -> tuple[str, str, str]:
    """Register and log in a fresh internal user."""
    email = f"t{uuid.uuid4().hex[:10]}@example.com"
    password = f"pw-{uuid.uuid4().hex}"
    client.post("/api/auth/register", json={"email": email, "password": password})
    token = client.post("/api/auth/login", json={"email": email, "password": password}).json()[
        "access_token"
    ]
    user_id = client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
    return token, user_id, email


def _join(
    client: TestClient, owner_token: str, member_token: str, member_id: str, group_id: str
) -> None:
    """Owner invites ``member_id``; the member accepts."""
    owner = {"Authorization": f"Bearer {owner_token}"}
    member = {"Authorization": f"Bearer {member_token}"}
    added = client.post(
        f"/api/spaces/{group_id}/members", headers=owner, json={"user_id": member_id}
    )
    assert added.status_code == 200, added.text
    accepted = client.post(f"/api/spaces/{group_id}/accept", headers=member)
    assert accepted.status_code == 200, accepted.text


def test_non_admin_directory_excludes_unshared_users(client: TestClient) -> None:
    """Non-admin GET /api/users only includes users sharing a group space."""
    token_a, user_a, _ = _user(client)
    token_b, user_b, email_b = _user(client)
    _token_c, user_c, _ = _user(client)
    auth_a = {"Authorization": f"Bearer {token_a}"}
    auth_b = {"Authorization": f"Bearer {token_b}"}

    # No shared group yet: A should not see B or C.
    alone = client.get("/api/users", headers=auth_a).json()
    alone_ids = {r["id"] for r in alone}
    assert user_b not in alone_ids
    assert user_c not in alone_ids
    assert "providers" not in (alone[0] if alone else {})

    group = client.post(
        "/api/spaces", headers=auth_a, json={"name": f"dir-{uuid.uuid4().hex[:8]}"}
    ).json()
    _join(client, token_a, token_b, user_b, group["id"])

    shared = client.get("/api/users", headers=auth_a).json()
    by_id = {r["id"]: r for r in shared}
    assert user_a in by_id
    assert user_b in by_id
    assert user_c not in by_id
    assert by_id[user_b]["email"] == email_b
    assert "providers" not in by_id[user_b]
    assert "created_at" not in by_id[user_b]

    # B sees A (shared group) but not C.
    from_b = {r["id"] for r in client.get("/api/users", headers=auth_b).json()}
    assert user_a in from_b
    assert user_b in from_b
    assert user_c not in from_b

    if ADMIN_KEY:
        admin_rows = client.get(
            "/api/users", headers={"Authorization": f"Bearer {ADMIN_KEY}"}
        ).json()
        admin_ids = {r["id"] for r in admin_rows}
        assert user_a in admin_ids
        assert user_b in admin_ids
        assert user_c in admin_ids
        sample = next(r for r in admin_rows if r["id"] == user_b)
        assert sample["providers"] == ["internal"]
        assert "created_at" in sample


def test_lookup_email_is_admin_only(client: TestClient) -> None:
    """Email lookup answers admins only; every other caller gets the same 404."""
    token_a, _, _ = _user(client)
    token_b, user_b, email_b = _user(client)
    token_c, user_c, email_c = _user(client)
    auth_a = {"Authorization": f"Bearer {token_a}"}
    auth_b = {"Authorization": f"Bearer {token_b}"}
    auth_c = {"Authorization": f"Bearer {token_c}"}

    # A creates a group (roster manager); B is an accepted member; C is a stranger.
    group = client.post(
        "/api/spaces", headers=auth_a, json={"name": f"lookup-{uuid.uuid4().hex[:8]}"}
    ).json()
    _join(client, token_a, token_b, user_b, group["id"])

    missing = f"nobody-{uuid.uuid4().hex}@example.com"
    for auth in (auth_a, auth_b, auth_c):
        for email in (email_c, email_c.upper(), email_b, missing):
            resp = client.get("/api/users/lookup", headers=auth, params={"email": email})
            assert resp.status_code == 404, (email, resp.text)

    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}
    hit = client.get("/api/users/lookup", headers=admin, params={"email": email_c.upper()})
    assert hit.status_code == 200
    assert hit.json()["id"] == user_c
    assert set(hit.json()) == {"id", "email", "display_name"}
    miss = client.get("/api/users/lookup", headers=admin, params={"email": missing})
    assert miss.status_code == 404


def test_lookup_requires_auth(client: TestClient) -> None:
    """Unauthenticated lookup is rejected."""
    assert client.get("/api/users/lookup?email=a@b.c").status_code == 401


def test_add_member_response_is_uniform_for_known_and_unknown_targets(
    client: TestClient,
) -> None:
    """A group creator cannot tell a registered address from an unregistered one."""
    token_a, user_a, _ = _user(client)
    token_b, _, email_b = _user(client)
    auth_a = {"Authorization": f"Bearer {token_a}"}
    auth_b = {"Authorization": f"Bearer {token_b}"}
    group = client.post(
        "/api/spaces", headers=auth_a, json={"name": f"uniform-{uuid.uuid4().hex[:8]}"}
    ).json()
    members = f"/api/spaces/{group['id']}/members"

    known = client.post(members, headers=auth_a, json={"email": email_b})
    unknown = client.post(
        members, headers=auth_a, json={"email": f"nobody-{uuid.uuid4().hex}@example.com"}
    )
    unknown_id = client.post(members, headers=auth_a, json={"user_id": str(uuid.uuid4())})
    for resp in (known, unknown, unknown_id):
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"status": "ok"}

    roster = client.get(f"/api/spaces/{group['id']}", headers=auth_a).json()["members"]
    assert {m["user_id"] for m in roster} == {user_a}
    invites = client.get("/api/me", headers=auth_b).json()["pending_invites"]
    assert [i["id"] for i in invites] == [group["id"]]
