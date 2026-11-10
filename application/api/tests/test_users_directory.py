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
    assert (
        client.post(
            f"/api/spaces/{group['id']}/members",
            headers=auth_a,
            json={"user_id": user_b},
        ).status_code
        == 200
    )

    shared = client.get("/api/users", headers=auth_a).json()
    by_id = {r["id"]: r for r in shared}
    assert user_a in by_id and user_b in by_id
    assert user_c not in by_id
    assert by_id[user_b]["email"] == email_b
    assert "providers" not in by_id[user_b]
    assert "created_at" not in by_id[user_b]

    # B sees A (shared group) but not C.
    from_b = {r["id"] for r in client.get("/api/users", headers=auth_b).json()}
    assert user_a in from_b and user_b in from_b
    assert user_c not in from_b

    if ADMIN_KEY:
        admin_rows = client.get(
            "/api/users", headers={"Authorization": f"Bearer {ADMIN_KEY}"}
        ).json()
        admin_ids = {r["id"] for r in admin_rows}
        assert user_a in admin_ids and user_b in admin_ids and user_c in admin_ids
        sample = next(r for r in admin_rows if r["id"] == user_b)
        assert sample["providers"] == ["internal"]
        assert "created_at" in sample


def test_lookup_email_for_manager_not_random_user(client: TestClient) -> None:
    """Group managers can lookup by email; non-managers get 404 (no enumeration)."""
    token_a, _user_a, _ = _user(client)
    token_b, user_b, email_b = _user(client)
    token_c, _user_c, email_c = _user(client)
    auth_a = {"Authorization": f"Bearer {token_a}"}
    auth_b = {"Authorization": f"Bearer {token_b}"}
    auth_c = {"Authorization": f"Bearer {token_c}"}

    # A creates a group → roster manager. B is only a member after invite.
    group = client.post(
        "/api/spaces", headers=auth_a, json={"name": f"lookup-{uuid.uuid4().hex[:8]}"}
    ).json()
    assert (
        client.post(
            f"/api/spaces/{group['id']}/members",
            headers=auth_a,
            json={"user_id": user_b},
        ).status_code
        == 200
    )

    # Manager A can look up stranger C by email.
    hit = client.get(
        f"/api/users/lookup?email={email_c}",
        headers=auth_a,
    )
    assert hit.status_code == 200
    body = hit.json()
    assert body["id"] == _user_c
    assert body["email"] == email_c
    assert "display_name" in body
    assert set(body) == {"id", "email", "display_name"}

    # Casefold match.
    upper = client.get(
        f"/api/users/lookup?email={email_c.upper()}",
        headers=auth_a,
    )
    assert upper.status_code == 200
    assert upper.json()["id"] == _user_c

    # Member B (not manager) looking up stranger C → 404.
    assert client.get(f"/api/users/lookup?email={email_c}", headers=auth_b).status_code == 404
    # Random user C looking up B → 404.
    assert client.get(f"/api/users/lookup?email={email_b}", headers=auth_c).status_code == 404
    # Miss and unauthorized share the same 404.
    assert (
        client.get(
            "/api/users/lookup?email=nobody-missing@example.com",
            headers=auth_a,
        ).status_code
        == 404
    )
    assert client.get("/api/users/lookup?email=x@y.z", headers=auth_c).status_code == 404

    if ADMIN_KEY:
        admin = client.get(
            f"/api/users/lookup?email={email_c}",
            headers={"Authorization": f"Bearer {ADMIN_KEY}"},
        )
        assert admin.status_code == 200
        assert admin.json()["email"] == email_c


def test_lookup_requires_auth(client: TestClient) -> None:
    """Unauthenticated lookup is rejected."""
    assert client.get("/api/users/lookup?email=a@b.c").status_code == 401
