"""Live integration tests against a running api (inside Compose by default).

Skipped when the api is unreachable. Override the target with ``MEMCHENTIC_TEST_URL``.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

BASE = os.environ.get("MEMCHENTIC_TEST_URL", "http://127.0.0.1:8080")
ADMIN_KEY = os.environ.get("MEMCHENTIC_ADMIN_API_KEY", "")
ADMIN_USER = os.environ.get("MEMCHENTIC_ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("MEMCHENTIC_ADMIN_PASSWORD", "")
ADMIN_EMAIL = os.environ.get("MEMCHENTIC_ADMIN_EMAIL", "").strip()


def _reachable() -> bool:
    """Report whether the api health endpoint answers.

    Returns:
        True when ``/api/health`` returns 200.
    """
    try:
        return httpx.get(f"{BASE}/api/health", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(not _reachable(), reason="api not reachable")


def _user(client: httpx.Client) -> tuple[str, str, str]:
    """Register and log in a fresh internal user.

    Args:
        client: HTTP client bound to the api.

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


def _tool(client: httpx.Client, token: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call an MCP tool over streamable HTTP.

    Args:
        client: HTTP client bound to the api.
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
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": name, "arguments": args}},
    )
    resp.raise_for_status()
    return resp.json()["result"]


@pytest.fixture
def client() -> httpx.Client:
    """HTTP client bound to the api under test.

    Returns:
        A configured ``httpx.Client``.
    """
    with httpx.Client(base_url=BASE, timeout=30) as c:
        yield c


def test_unauthenticated_mcp_gets_resource_metadata_challenge(client: httpx.Client) -> None:
    """MCP without credentials returns 401 pointing at protected resource metadata."""
    resp = client.post("/mcp", json={})
    assert resp.status_code == 401
    assert "resource_metadata=" in resp.headers["www-authenticate"]


def test_tool_surface_is_memchentic_native(client: httpx.Client) -> None:
    """The MCP server exposes exactly the documented v1 tool names."""
    token, _, _ = _user(client)
    resp = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    names = {t["name"] for t in resp.json()["result"]["tools"]}
    assert names == {
        "me", "space_list", "space_create", "space_show", "remember", "find", "open",
        "revise", "forget", "browse", "since", "by_tag", "relocate", "guide_write",
        "guide_find", "about_path", "trace", "overlap_report",
    }


def test_personal_isolation_and_team_lifecycle(client: httpx.Client) -> None:
    """Personal rows stay private; team rows are shared only while a member."""
    token_a, _, _ = _user(client)
    token_b, user_b, email_b = _user(client)
    marker = uuid.uuid4().hex

    entry = _tool(client, token_a, "remember", {"title": f"private {marker}", "body": marker})
    entry_id = entry["structuredContent"]["id"]
    found_b = _tool(client, token_b, "find", {"query": marker})["structuredContent"]["result"]
    assert entry_id not in {d["id"] for d in found_b}
    assert _tool(client, token_b, "open", {"id": entry_id}).get("isError") is True

    team = _tool(client, token_a, "space_create", {"name": f"team {marker}"})["structuredContent"]
    auth_a = {"Authorization": f"Bearer {token_a}"}
    auth_b = {"Authorization": f"Bearer {token_b}"}
    assert client.post(f"/api/spaces/{team['id']}/members", headers=auth_a,
                       json={"email": email_b}).status_code == 200
    shared = _tool(client, token_a, "remember",
                   {"title": f"shared {marker}", "body": marker, "space_id": team["id"]})
    shared_id = shared["structuredContent"]["id"]
    assert _tool(client, token_b, "open", {"id": shared_id})["structuredContent"]["id"] == shared_id

    assert client.post(f"/api/spaces/{team['id']}/members", headers=auth_b,
                       json={"user_id": user_b}).status_code == 403
    assert client.delete(f"/api/spaces/{team['id']}/members/{user_b}",
                         headers=auth_b).status_code == 200
    assert _tool(client, token_b, "open", {"id": shared_id}).get("isError") is True


def test_authenticated_user_directory_omits_admin_fields(client: httpx.Client) -> None:
    """Non-admins can list users for team invites without provider/created_at."""
    token_a, user_a, _ = _user(client)
    token_b, user_b, email_b = _user(client)
    rows = client.get("/api/users", headers={"Authorization": f"Bearer {token_a}"}).json()
    assert isinstance(rows, list)
    by_id = {r["id"]: r for r in rows}
    assert user_a in by_id and user_b in by_id
    assert by_id[user_b]["email"] == email_b
    assert "provider" not in by_id[user_b]
    assert "created_at" not in by_id[user_b]
    if ADMIN_KEY:
        admin_rows = client.get(
            "/api/users", headers={"Authorization": f"Bearer {ADMIN_KEY}"}
        ).json()
        sample = next(r for r in admin_rows if r["id"] == user_b)
        assert "provider" in sample
        assert "created_at" in sample


def test_write_target_resolution(client: httpx.Client) -> None:
    """space_id beats team; team names and visibility=shared resolve without guessing."""
    token, _, _ = _user(client)
    auth = {"Authorization": f"Bearer {token}"}
    marker = uuid.uuid4().hex

    def write(**target: Any) -> httpx.Response:
        return client.post("/api/memories", headers=auth, json={
            "title": f"t {uuid.uuid4().hex}", "body": f"{marker} {uuid.uuid4().hex}",
            "force": True, **target,
        })

    me = client.get("/api/me", headers=auth).json()
    assert write().json()["space_id"] == me["personal_space_id"]

    ops = client.post("/api/spaces", headers=auth, json={"name": f"Ops {marker}"}).json()
    assert write(visibility="shared").json()["space_id"] == ops["id"]
    assert write(team=f"ops {marker}").json()["space_id"] == ops["id"]

    home = client.post("/api/spaces", headers=auth, json={"name": f"Home {marker}"}).json()
    assert write(space_id=home["id"], team=f"Ops {marker}").json()["space_id"] == home["id"]
    assert write(visibility="shared").status_code == 422
    assert write(team=f"missing {marker}").status_code == 422

    assert client.patch("/api/me", headers=auth,
                        json={"default_shared_space_id": home["id"]}).status_code == 200
    assert write(visibility="shared").json()["space_id"] == home["id"]
    assert write(visibility="personal").json()["space_id"] == me["personal_space_id"]

    client.post("/api/spaces", headers=auth, json={"name": f"OPS {marker}"})
    assert write(team=f"Ops {marker}").status_code == 422

    outsider, _, _ = _user(client)
    other = client.post("/api/spaces", headers={"Authorization": f"Bearer {outsider}"},
                        json={"name": f"Other {marker}"}).json()
    assert client.patch("/api/me", headers=auth,
                        json={"default_shared_space_id": other["id"]}).status_code == 403


def test_admin_key_write_requires_space_id(client: httpx.Client) -> None:
    """The env admin key has no personal space and gets no name/default resolution."""
    if not ADMIN_KEY:
        pytest.skip("MEMCHENTIC_ADMIN_API_KEY not set")
    auth = {"Authorization": f"Bearer {ADMIN_KEY}"}
    for target in ({}, {"visibility": "shared"}, {"team": "anything"}):
        resp = client.post("/api/memories", headers=auth,
                           json={"title": "admin", "body": uuid.uuid4().hex, **target})
        assert resp.status_code == 403


def test_api_keys_bind_to_user_and_admin_key_is_admin(client: httpx.Client) -> None:
    """A per-user key acts as that user; the env admin key acts as admin."""
    token, _, email = _user(client)
    raw = client.post("/api/api-keys", headers={"Authorization": f"Bearer {token}"},
                      json={"name": "test"}).json()["key"]
    assert _tool(client, raw, "me", {})["structuredContent"]["email"] == email
    if ADMIN_KEY:
        assert _tool(client, ADMIN_KEY, "me", {})["structuredContent"]["is_admin"] is True


def test_api_key_cycle_is_own_only_and_admin_can_cycle_others(client: httpx.Client) -> None:
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

    assert client.post(
        "/api/api-keys/cycle", headers=auth_b, json={"user_id": user_a}
    ).status_code == 403

    if not ADMIN_KEY:
        return
    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}
    admin_list = client.get("/api/api-keys", headers=admin).json()
    assert user_a in {row["user_id"] for row in admin_list}
    assert user_b not in {row["user_id"] for row in admin_list}
    cycled = client.post(
        "/api/api-keys/cycle", headers=admin, json={"user_id": user_b}
    ).json()
    assert cycled["user_id"] == user_b
    assert client.get("/api/api-keys", headers=auth_b).json()[0]["prefix"] == cycled["prefix"]
    assert user_b in {
        row["user_id"] for row in client.get("/api/api-keys", headers=admin).json()
    }


def test_near_duplicate_refused_without_force(client: httpx.Client) -> None:
    """A second identical entry is refused unless ``force`` is set."""
    token, _, _ = _user(client)
    auth = {"Authorization": f"Bearer {token}"}
    body = {"title": "Database choice", "body": f"Postgres with pgvector {uuid.uuid4().hex}"}
    assert client.post("/api/memories", headers=auth, json=body).status_code == 200
    assert client.post("/api/memories", headers=auth, json=body).status_code == 409
    assert client.post("/api/memories", headers=auth,
                       json={**body, "force": True}).status_code == 200


def _pkce_pair() -> tuple[str, str]:
    """Build a PKCE verifier and its S256 challenge.

    Returns:
        Tuple of (verifier, challenge).
    """
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def test_authorize_requires_registered_redirect_and_pkce(client: httpx.Client) -> None:
    """Authorize rejects unregistered redirects and missing PKCE; tokens need the verifier."""
    password = "pw-oauth-" + uuid.uuid4().hex
    email = f"o{uuid.uuid4().hex[:10]}@example.com"
    client.post("/api/auth/register", json={"email": email, "password": password})
    redirect = "http://127.0.0.1:9/cb"
    client_id = client.post("/oauth/register", json={
        "client_name": "Test Harness", "redirect_uris": [redirect]}).json()["client_id"]
    verifier, challenge = _pkce_pair()
    base = {"client_id": client_id, "redirect_uri": redirect, "response_type": "code",
            "code_challenge_method": "S256"}

    assert client.get("/oauth/authorize", params={**base, "code_challenge": ""}).status_code == 400
    assert client.get("/oauth/authorize", params={
        **base, "code_challenge": challenge, "redirect_uri": "http://evil.example/cb",
    }).status_code == 400
    page = client.get("/oauth/authorize", params={**base, "code_challenge": challenge})
    assert page.status_code == 200
    assert "Test Harness" in page.text and "127.0.0.1:9" in page.text
    assert page.headers["x-frame-options"] == "DENY"

    form = {"email": email, "password": password, "client_id": client_id,
            "redirect_uri": redirect, "code_challenge": challenge,
            "code_challenge_method": "S256"}
    redirected = client.post("/oauth/authorize", data=form, follow_redirects=False)
    assert redirected.status_code == 302
    code = parse_qs(urlparse(redirected.headers["location"]).query)["code"][0]

    grant = {"grant_type": "authorization_code", "code": code, "client_id": client_id,
             "redirect_uri": redirect}
    assert client.post("/oauth/token", data={**grant, "code_verifier": "x" * 50}).status_code == 400
    missing_redirect = {k: v for k, v in grant.items() if k != "redirect_uri"}
    assert client.post("/oauth/token",
                       data={**missing_redirect, "code_verifier": verifier}).status_code == 400
    assert client.post("/oauth/token", data={**grant, "code_verifier": verifier}).status_code == 200
    assert client.post("/oauth/token", data={**grant, "code_verifier": verifier}).status_code == 400


def test_authorize_accepts_admin_username(client: httpx.Client) -> None:
    """Break-glass admin can Authorize with username (and email when configured)."""
    if not ADMIN_PASSWORD:
        pytest.skip("MEMCHENTIC_ADMIN_PASSWORD unset")
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
    assert "Email or admin user" in page.text
    assert 'type="email"' not in page.text

    form = {
        "email": ADMIN_USER,
        "password": ADMIN_PASSWORD,
        "client_id": client_id,
        "redirect_uri": redirect,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
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
    assert me.json()["is_admin"] is True

    if ADMIN_EMAIL:
        form_email = {**form, "email": ADMIN_EMAIL}
        again = client.post("/oauth/authorize", data=form_email, follow_redirects=False)
        assert again.status_code == 302


def test_roster_rules_and_no_email_enumeration(client: httpx.Client) -> None:
    """Only the creator manages the roster; outsiders cannot probe which emails exist."""
    token_a, user_a, _ = _user(client)
    token_b, _, email_b = _user(client)
    token_c, user_c, email_c = _user(client)
    auth = {t: {"Authorization": f"Bearer {t}"} for t in (token_a, token_b, token_c)}
    team = _tool(client, token_a, "space_create", {"name": f"roster {uuid.uuid4().hex}"})
    members = f"/api/spaces/{team['structuredContent']['id']}/members"

    outsider_known = client.post(members, headers=auth[token_c], json={"email": email_b})
    outsider_unknown = client.post(members, headers=auth[token_c],
                                   json={"email": f"nobody{uuid.uuid4().hex}@example.com"})
    assert outsider_known.status_code == outsider_unknown.status_code == 403

    for email in (email_b, email_c):
        assert client.post(members, headers=auth[token_a], json={"email": email}).status_code == 200
    assert client.delete(f"{members}/{user_c}", headers=auth[token_b]).status_code == 403
    if ADMIN_KEY:
        admin = {"Authorization": f"Bearer {ADMIN_KEY}"}
        assert client.delete(f"{members}/{user_a}", headers=admin).status_code in (400, 403)


def test_non_admin_cannot_issue_keys_for_another_user(client: httpx.Client) -> None:
    """A user-supplied user_id on key issue is refused for non-admins."""
    token_a, _, _ = _user(client)
    _, user_b, _ = _user(client)
    issued = client.post("/api/api-keys", headers={"Authorization": f"Bearer {token_a}"},
                         json={"name": "steal", "user_id": user_b})
    assert issued.status_code == 403
