"""HTTP/MCP integration tests against the in-process app / ``memchentic_test``."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@example.com").strip()
_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _oauth_csrf(html: str) -> str:
    match = _CSRF_RE.search(html)
    assert match, "csrf_token missing from authorize page"
    return match.group(1)


def _user(client: TestClient) -> tuple[str, str, str]:
    """Register and log in a fresh internal user.

    Args:
        client: In-process test client.

    Returns:
        Tuple of (access token, user id, email).
    """
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
    resp = client.post(
        "/mcp",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        },
    )
    resp.raise_for_status()
    return resp.json()["result"]


def test_unauthenticated_mcp_gets_resource_metadata_challenge(client: TestClient) -> None:
    """MCP without credentials returns 401 pointing at protected resource metadata."""
    resp = client.post("/mcp", json={})
    assert resp.status_code == 401
    assert "resource_metadata=" in resp.headers["www-authenticate"]


def test_tool_surface_is_memchentic_native(client: TestClient) -> None:
    """The MCP server exposes exactly the documented v1 tool names."""
    token, _, _ = _user(client)
    resp = client.post(
        "/mcp",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    names = {t["name"] for t in resp.json()["result"]["tools"]}
    assert names == {
        "me",
        "space_list",
        "space_create",
        "space_show",
        "remember",
        "find",
        "open",
        "revise",
        "forget",
        "browse",
        "since",
        "by_tag",
        "relocate",
        "guide_write",
        "guide_find",
        "about_path",
        "trace",
        "overlap_report",
    }


def test_personal_isolation_and_group_lifecycle(client: TestClient) -> None:
    """Personal rows stay private; group rows are shared only while a member."""
    token_a, _, _ = _user(client)
    token_b, user_b, email_b = _user(client)
    marker = uuid.uuid4().hex

    entry = _tool(client, token_a, "remember", {"title": f"private {marker}", "body": marker})
    entry_id = entry["structuredContent"]["id"]
    found_b = _tool(client, token_b, "find", {"query": marker})["structuredContent"]["result"]
    assert entry_id not in {d["id"] for d in found_b}
    assert _tool(client, token_b, "open", {"id": entry_id}).get("isError") is True

    group = _tool(client, token_a, "space_create", {"name": f"group {marker}"})["structuredContent"]
    auth_a = {"Authorization": f"Bearer {token_a}"}
    auth_b = {"Authorization": f"Bearer {token_b}"}
    assert (
        client.post(
            f"/api/spaces/{group['id']}/members", headers=auth_a, json={"email": email_b}
        ).status_code
        == 200
    )
    assert client.post(f"/api/spaces/{group['id']}/accept", headers=auth_b).status_code == 200
    shared = _tool(
        client,
        token_a,
        "remember",
        {"title": f"shared {marker}", "body": marker, "space_id": group["id"]},
    )
    shared_id = shared["structuredContent"]["id"]
    assert _tool(client, token_b, "open", {"id": shared_id})["structuredContent"]["id"] == shared_id

    assert (
        client.post(
            f"/api/spaces/{group['id']}/members", headers=auth_b, json={"user_id": user_b}
        ).status_code
        == 403
    )
    assert (
        client.delete(f"/api/spaces/{group['id']}/members/{user_b}", headers=auth_b).status_code
        == 200
    )
    assert _tool(client, token_b, "open", {"id": shared_id}).get("isError") is True


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
    if ADMIN_KEY:
        admin_rows = client.get(
            "/api/users", headers={"Authorization": f"Bearer {ADMIN_KEY}"}
        ).json()
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

    if ADMIN_KEY:
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


def test_write_target_resolution(client: TestClient) -> None:
    """space_id beats group; group names and visibility=shared resolve without guessing."""
    token, _, _ = _user(client)
    auth = {"Authorization": f"Bearer {token}"}
    marker = uuid.uuid4().hex

    def write(**target: Any) -> Any:
        return client.post(
            "/api/memories",
            headers=auth,
            json={
                "title": f"t {uuid.uuid4().hex}",
                "body": f"{marker} {uuid.uuid4().hex}",
                "force": True,
                **target,
            },
        )

    me = client.get("/api/me", headers=auth).json()
    assert write().json()["space_id"] == me["personal_space_id"]

    ops = client.post("/api/spaces", headers=auth, json={"name": f"Ops {marker}"}).json()
    assert write(visibility="shared").json()["space_id"] == ops["id"]
    assert write(group=f"ops {marker}").json()["space_id"] == ops["id"]

    home = client.post("/api/spaces", headers=auth, json={"name": f"Home {marker}"}).json()
    assert write(space_id=home["id"], group=f"Ops {marker}").json()["space_id"] == home["id"]
    assert write(visibility="shared").status_code == 422
    assert write(group=f"missing {marker}").status_code == 422

    assert (
        client.patch(
            "/api/me", headers=auth, json={"default_shared_space_id": home["id"]}
        ).status_code
        == 200
    )
    assert write(visibility="shared").json()["space_id"] == home["id"]
    assert write(visibility="personal").json()["space_id"] == me["personal_space_id"]

    client.post("/api/spaces", headers=auth, json={"name": f"OPS {marker}"})
    assert write(group=f"Ops {marker}").status_code == 422

    outsider, _, _ = _user(client)
    other = client.post(
        "/api/spaces",
        headers={"Authorization": f"Bearer {outsider}"},
        json={"name": f"Other {marker}"},
    ).json()
    assert (
        client.patch(
            "/api/me", headers=auth, json={"default_shared_space_id": other["id"]}
        ).status_code
        == 404
    )


def test_admin_key_writes_as_admin_user(client: TestClient) -> None:
    """The env admin key is a real user: personal writes work and stamp created_by."""
    if not ADMIN_KEY:
        pytest.skip("ADMIN_API_KEY not set")
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
    if ADMIN_KEY:
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

    if not ADMIN_KEY:
        return
    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}
    admin_list = client.get("/api/api-keys", headers=admin).json()
    assert user_a in {row["user_id"] for row in admin_list}
    assert user_b not in {row["user_id"] for row in admin_list}
    cycled = client.post("/api/api-keys/cycle", headers=admin, json={"user_id": user_b}).json()
    assert cycled["user_id"] == user_b
    assert client.get("/api/api-keys", headers=auth_b).json()[0]["prefix"] == cycled["prefix"]
    assert user_b in {row["user_id"] for row in client.get("/api/api-keys", headers=admin).json()}


def test_near_duplicate_refused_without_force(client: TestClient) -> None:
    """A second identical entry is refused unless ``force`` is set."""
    token, _, _ = _user(client)
    auth = {"Authorization": f"Bearer {token}"}
    body = {"title": "Database choice", "body": f"Postgres with pgvector {uuid.uuid4().hex}"}
    assert client.post("/api/memories", headers=auth, json=body).status_code == 200
    assert client.post("/api/memories", headers=auth, json=body).status_code == 409
    assert (
        client.post("/api/memories", headers=auth, json={**body, "force": True}).status_code == 200
    )


def _pkce_pair() -> tuple[str, str]:
    """Build a PKCE verifier and its S256 challenge.

    Returns:
        Tuple of (verifier, challenge).
    """
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def test_authorize_requires_registered_redirect_and_pkce(client: TestClient) -> None:
    """Authorize rejects unregistered redirects and missing PKCE; tokens need the verifier."""
    password = "pw-oauth-" + uuid.uuid4().hex
    email = f"o{uuid.uuid4().hex[:10]}@example.com"
    client.post("/api/auth/register", json={"email": email, "password": password})
    redirect = "http://127.0.0.1:9/cb"
    client_id = client.post(
        "/oauth/register", json={"client_name": "Test Harness", "redirect_uris": [redirect]}
    ).json()["client_id"]
    verifier, challenge = _pkce_pair()
    base = {
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "code_challenge_method": "S256",
    }

    assert client.get("/oauth/authorize", params={**base, "code_challenge": ""}).status_code == 400
    assert (
        client.get(
            "/oauth/authorize",
            params={
                **base,
                "code_challenge": challenge,
                "redirect_uri": "http://evil.example/cb",
            },
        ).status_code
        == 400
    )
    page = client.get("/oauth/authorize", params={**base, "code_challenge": challenge})
    assert page.status_code == 200
    assert "Test Harness" in page.text
    assert "127.0.0.1:9" in page.text
    assert page.headers["x-frame-options"] == "DENY"

    form = {
        "email": email,
        "password": password,
        "client_id": client_id,
        "redirect_uri": redirect,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "csrf_token": _oauth_csrf(page.text),
    }
    redirected = client.post("/oauth/authorize", data=form, follow_redirects=False)
    assert redirected.status_code == 302
    code = parse_qs(urlparse(redirected.headers["location"]).query)["code"][0]

    grant = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "redirect_uri": redirect,
    }
    assert client.post("/oauth/token", data={**grant, "code_verifier": "x" * 50}).status_code == 400
    missing_redirect = {k: v for k, v in grant.items() if k != "redirect_uri"}
    assert (
        client.post(
            "/oauth/token", data={**missing_redirect, "code_verifier": verifier}
        ).status_code
        == 400
    )
    assert client.post("/oauth/token", data={**grant, "code_verifier": verifier}).status_code == 200
    assert client.post("/oauth/token", data={**grant, "code_verifier": verifier}).status_code == 400


def test_authorize_accepts_admin_email(client: TestClient) -> None:
    """Break-glass admin can Authorize with ADMIN_EMAIL + password.

    When a DB user already exists for that email with the same password, the
    DB user wins (see prefer-user tests). This harness only asserts the
    authorize form is email-typed and credentials are accepted.
    """
    if not ADMIN_PASSWORD or not ADMIN_EMAIL:
        pytest.skip("ADMIN_PASSWORD / ADMIN_EMAIL unset")
    redirect = "http://127.0.0.1:9/cb"
    client_id = client.post(
        "/oauth/register",
        json={"client_name": "Admin Harness", "redirect_uris": [redirect]},
    ).json()["client_id"]
    verifier, challenge = _pkce_pair()
    page = client.get(
        "/oauth/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    assert page.status_code == 200
    assert "<label>Email</label>" in page.text
    assert 'type="email"' in page.text
    assert "admin alias" not in page.text

    form = {
        "email": ADMIN_EMAIL,
        "password": ADMIN_PASSWORD,
        "client_id": client_id,
        "redirect_uri": redirect,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "csrf_token": _oauth_csrf(page.text),
    }
    redirected = client.post("/oauth/authorize", data=form, follow_redirects=False)
    assert redirected.status_code == 302
    code = parse_qs(urlparse(redirected.headers["location"]).query)["code"][0]
    token = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": redirect,
            "code_verifier": verifier,
        },
    )
    assert token.status_code == 200
    access = token.json()["access_token"]
    me = client.get("/api/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200
    # Admin email resolves to the real admin user (is_admin + durable id).
    body = me.json()
    assert body.get("email", "").casefold() == ADMIN_EMAIL.casefold()
    assert body.get("is_admin") is True
    assert body.get("id")


def test_roster_rules_and_no_email_enumeration(client: TestClient) -> None:
    """Only the creator manages the roster; outsiders cannot probe which emails exist."""
    token_a, user_a, _ = _user(client)
    token_b, _, email_b = _user(client)
    token_c, user_c, email_c = _user(client)
    auth = {t: {"Authorization": f"Bearer {t}"} for t in (token_a, token_b, token_c)}
    group = _tool(client, token_a, "space_create", {"name": f"roster {uuid.uuid4().hex}"})
    members = f"/api/spaces/{group['structuredContent']['id']}/members"

    outsider_known = client.post(members, headers=auth[token_c], json={"email": email_b})
    outsider_unknown = client.post(
        members, headers=auth[token_c], json={"email": f"nobody{uuid.uuid4().hex}@example.com"}
    )
    assert outsider_known.status_code == outsider_unknown.status_code == 404

    for email in (email_b, email_c):
        assert client.post(members, headers=auth[token_a], json={"email": email}).status_code == 200
    assert client.delete(f"{members}/{user_c}", headers=auth[token_b]).status_code == 404
    if ADMIN_KEY:
        admin = {"Authorization": f"Bearer {ADMIN_KEY}"}
        assert client.delete(f"{members}/{user_a}", headers=admin).status_code in (400, 403)


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


def test_shared_write_is_refused_for_a_group_the_caller_did_not_join(
    client: TestClient,
) -> None:
    """Being added to a group must not redirect visibility=shared writes."""
    token_a, _, _ = _user(client)
    token_b, user_b, _ = _user(client)
    auth_a = {"Authorization": f"Bearer {token_a}"}
    auth_b = {"Authorization": f"Bearer {token_b}"}
    group = client.post(
        "/api/spaces", headers=auth_a, json={"name": f"capture-{uuid.uuid4().hex[:8]}"}
    ).json()
    assert (
        client.post(
            f"/api/spaces/{group['id']}/members", headers=auth_a, json={"user_id": user_b}
        ).status_code
        == 200
    )
    write = client.post(
        "/api/memories",
        headers=auth_b,
        json={"title": f"t {uuid.uuid4().hex}", "body": "b", "visibility": "shared", "force": True},
    )
    assert write.status_code == 422, write.text


def test_added_member_must_accept_before_group_is_visible(client: TestClient) -> None:
    """An invite grants nothing until the invited user accepts it."""
    token_a, user_a, _ = _user(client)
    token_b, user_b, email_b = _user(client)
    auth_a = {"Authorization": f"Bearer {token_a}"}
    auth_b = {"Authorization": f"Bearer {token_b}"}
    marker = uuid.uuid4().hex
    group = client.post("/api/spaces", headers=auth_a, json={"name": f"invite {marker}"}).json()
    gid = group["id"]
    assert (
        client.post(
            f"/api/spaces/{gid}/members", headers=auth_a, json={"email": email_b}
        ).status_code
        == 200
    )
    guide = client.post(
        "/api/guides",
        headers=auth_a,
        json={"title": f"guide {marker}", "body": f"body {marker}", "space_id": gid},
    ).json()

    def b_guide_ids() -> set[str]:
        found = _tool(client, token_b, "guide_find", {"query": f"guide {marker}"})
        return {d["id"] for d in found["structuredContent"]["result"]}

    def b_write(**target: Any) -> int:
        return client.post(
            "/api/memories",
            headers=auth_b,
            json={"title": f"t {uuid.uuid4().hex}", "body": marker, "force": True, **target},
        ).status_code

    # Before accept: nothing from the group reaches B, and B cannot write into it.
    me_b = client.get("/api/me", headers=auth_b).json()
    assert gid not in {m["id"] for m in me_b["memberships"]}
    assert [i["id"] for i in me_b["pending_invites"]] == [gid]
    assert guide["id"] not in b_guide_ids()
    assert _tool(client, token_b, "open", {"id": guide["id"]}).get("isError") is True
    assert client.get(f"/api/spaces/{gid}", headers=auth_b).status_code == 404
    assert b_write(visibility="shared") == 422
    assert b_write(space_id=gid) == 404
    mcp_me = _tool(client, token_b, "me", {})["structuredContent"]
    assert mcp_me["pending_invite_count"] == 1
    assert "pending_invites" not in mcp_me
    roster = client.get(f"/api/spaces/{gid}", headers=auth_a).json()["members"]
    assert {m["user_id"] for m in roster} == {user_a}
    assert user_b not in {u["id"] for u in client.get("/api/users", headers=auth_a).json()}

    # Accept (idempotent): the group opens up.
    for _ in range(2):
        accepted = client.post(f"/api/spaces/{gid}/accept", headers=auth_b)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["id"] == gid
    me_b = client.get("/api/me", headers=auth_b).json()
    assert gid in {m["id"] for m in me_b["memberships"]}
    assert me_b["pending_invites"] == []
    assert guide["id"] in b_guide_ids()
    shared = client.post(
        "/api/memories",
        headers=auth_b,
        json={"title": f"s {marker}", "body": marker, "visibility": "shared", "force": True},
    )
    assert shared.status_code == 200, shared.text
    assert shared.json()["space_id"] == gid
    roster = client.get(f"/api/spaces/{gid}", headers=auth_a).json()["members"]
    assert {m["user_id"] for m in roster} == {user_a, user_b}
    assert all(m["pending"] is False for m in roster)


def test_invite_can_be_declined_and_cannot_be_accepted_by_others(client: TestClient) -> None:
    """Only the invited user can accept; declining removes the invite."""
    token_a, _, _ = _user(client)
    token_b, user_b, _ = _user(client)
    token_c, _, _ = _user(client)
    auth_a = {"Authorization": f"Bearer {token_a}"}
    auth_b = {"Authorization": f"Bearer {token_b}"}
    auth_c = {"Authorization": f"Bearer {token_c}"}
    group = client.post(
        "/api/spaces", headers=auth_a, json={"name": f"decline-{uuid.uuid4().hex[:8]}"}
    ).json()
    gid = group["id"]
    assert (
        client.post(
            f"/api/spaces/{gid}/members", headers=auth_a, json={"user_id": user_b}
        ).status_code
        == 200
    )

    assert client.post(f"/api/spaces/{gid}/accept", headers=auth_c).status_code == 404
    # C cannot see the group: SEC-051 maps "Group not found" to 404.
    assert client.delete(f"/api/spaces/{gid}/members/{user_b}", headers=auth_c).status_code == 404

    assert client.delete(f"/api/spaces/{gid}/members/{user_b}", headers=auth_b).status_code == 200
    assert client.get("/api/me", headers=auth_b).json()["pending_invites"] == []
    assert client.post(f"/api/spaces/{gid}/accept", headers=auth_b).status_code == 404
