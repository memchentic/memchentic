"""Scoped user directory and email lookup for roster managers."""

from __future__ import annotations

import os
import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import event

from memchentic.db.session import get_engine
from support import register_user

ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "")


def _user(client: TestClient) -> tuple[str, str, str]:
    """Register and log in a fresh internal user."""
    user = register_user(client)
    return user.token, user.user_id, user.email


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

    admin_rows = client.get("/api/users", headers={"Authorization": f"Bearer {ADMIN_KEY}"}).json()
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


def test_admin_user_list_statement_count_does_not_grow_per_user(client: TestClient) -> None:
    """GET /api/users for an administrator does not add a statement per user."""
    token, user_id, _ = _user(client)
    promoted = client.post(
        "/api/auth-groups/administrators/members",
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
        json={"user_id": user_id},
    )
    assert promoted.status_code == 200, promoted.text
    auth = {"Authorization": f"Bearer {token}"}

    def count_statements() -> int:
        statements: list[str] = []

        def before(*args: Any) -> None:
            statements.append(args[2])

        engine = get_engine().sync_engine
        event.listen(engine, "before_cursor_execute", before)
        try:
            resp = client.get("/api/users", headers=auth)
        finally:
            event.remove(engine, "before_cursor_execute", before)
        assert resp.status_code == 200, resp.text
        assert any(row["id"] == user_id and "deletable" in row for row in resp.json())
        return len(statements)

    first = count_statements()
    for _ in range(5):
        _user(client)
    second = count_statements()
    assert second - first <= 2


def test_authenticated_user_directory_omits_admin_fields(client: TestClient) -> None:
    """Non-admins see shared-group peers without providers/created_at."""
    token_a, user_a, _ = _user(client)
    token_b, user_b, email_b = _user(client)
    auth_a = {"Authorization": f"Bearer {token_a}"}
    group = client.post(
        "/api/spaces", headers=auth_a, json={"name": f"dir-{uuid.uuid4().hex[:8]}"}
    ).json()
    _join(client, token_a, token_b, user_b, group["id"])
    rows = client.get("/api/users", headers=auth_a).json()
    assert isinstance(rows, list)
    by_id = {r["id"]: r for r in rows}
    assert user_a in by_id
    assert user_b in by_id
    assert by_id[user_b]["email"] == email_b
    assert "providers" not in by_id[user_b]
    assert "created_at" not in by_id[user_b]
    admin_rows = client.get("/api/users", headers={"Authorization": f"Bearer {ADMIN_KEY}"}).json()
    sample = next(r for r in admin_rows if r["id"] == user_b)
    assert sample["providers"] == ["internal"]
    assert "created_at" in sample


def test_user_detail_groups_are_acl_scoped(client: TestClient) -> None:
    """User detail lists only groups the viewer may see; personal is omitted."""
    token_a, user_a, _ = _user(client)
    token_b, user_b, email_b = _user(client)
    token_c, _, _ = _user(client)
    auth_a = {"Authorization": f"Bearer {token_a}"}
    auth_b = {"Authorization": f"Bearer {token_b}"}
    auth_c = {"Authorization": f"Bearer {token_c}"}

    shared = client.post(
        "/api/spaces", headers=auth_a, json={"name": f"shared-{uuid.uuid4().hex[:8]}"}
    ).json()
    private = client.post(
        "/api/spaces", headers=auth_a, json={"name": f"private-{uuid.uuid4().hex[:8]}"}
    ).json()
    _join(client, token_a, token_b, user_b, shared["id"])

    detail_b = client.get(f"/api/users/{user_b}", headers=auth_a).json()
    assert detail_b["id"] == user_b
    assert detail_b["email"] == email_b
    assert detail_b["display_name"]
    assert "providers" not in detail_b
    assert "created_at" not in detail_b
    group_ids = {t["id"] for t in detail_b["groups"]}
    assert shared["id"] in group_ids
    assert private["id"] not in group_ids
    assert all(t["kind"] == "group" for t in detail_b["groups"])

    outsider = client.get(f"/api/users/{user_a}", headers=auth_c)
    assert outsider.status_code == 404

    as_b = client.get(f"/api/users/{user_a}", headers=auth_b).json()
    assert {t["id"] for t in as_b["groups"]} == {shared["id"]}

    missing = client.get(f"/api/users/{uuid.uuid4()}", headers=auth_a)
    assert missing.status_code == 404

    admin = client.get(
        f"/api/users/{user_a}", headers={"Authorization": f"Bearer {ADMIN_KEY}"}
    ).json()
    assert admin["providers"] == ["internal"]
    assert "created_at" in admin
    admin_ids = {t["id"] for t in admin["groups"]}
    assert shared["id"] in admin_ids
    assert private["id"] in admin_ids


def test_user_detail_acl_edges(client: TestClient) -> None:
    """User detail requires auth, rejects bad ids, and hides deleted groups."""
    ghost = uuid.uuid4()
    assert client.get(f"/api/users/{ghost}").status_code == 401

    token_a, user_a, _ = _user(client)
    token_b, user_b, _ = _user(client)
    auth_a = {"Authorization": f"Bearer {token_a}"}
    auth_b = {"Authorization": f"Bearer {token_b}"}

    assert client.get("/api/users/not-a-uuid", headers=auth_a).status_code == 422
    assert client.get(f"/api/users/{ghost}", headers=auth_a).status_code == 404

    both = client.post(
        "/api/spaces", headers=auth_a, json={"name": f"both-{uuid.uuid4().hex[:6]}"}
    ).json()
    solo = client.post(
        "/api/spaces", headers=auth_a, json={"name": f"solo-{uuid.uuid4().hex[:6]}"}
    ).json()
    _join(client, token_a, token_b, user_b, both["id"])

    mutual = client.get(f"/api/users/{user_b}", headers=auth_a).json()
    assert {t["id"] for t in mutual["groups"]} == {both["id"]}

    self_b = client.get(f"/api/users/{user_b}", headers=auth_b).json()
    assert {t["id"] for t in self_b["groups"]} == {both["id"]}

    self_a = client.get(f"/api/users/{user_a}", headers=auth_a).json()
    assert {both["id"], solo["id"]} <= {t["id"] for t in self_a["groups"]}

    # Non-owner cannot delete; owner can. After the shared group is gone,
    # neither peer remains visible in the other's directory detail.
    assert client.delete(f"/api/spaces/{both['id']}", headers=auth_b).status_code == 403
    assert client.delete(f"/api/spaces/{both['id']}", headers=auth_a).status_code == 200
    assert client.get(f"/api/users/{user_b}", headers=auth_a).status_code == 404
