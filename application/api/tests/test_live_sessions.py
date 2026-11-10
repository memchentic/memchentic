"""HTTP session tests (remember me) against the in-process app / memchentic_test."""

from __future__ import annotations

import os
import uuid

from fastapi.testclient import TestClient

ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "")
GUI = {"X-Memchentic-Client": "gui"}


def _login(client: TestClient, *, remember: bool) -> tuple[str, str]:
    """Register a fresh user and log in.

    Args:
        client: In-process test client.
        remember: Whether to request a persistent session.

    Returns:
        Tuple of (raw Set-Cookie header, session cookie value).
    """
    email = f"s{uuid.uuid4().hex[:10]}@example.com"
    password = f"pw-{uuid.uuid4().hex}"
    client.post("/api/auth/register", json={"email": email, "password": password})
    res = client.post(
        "/api/auth/login", json={"email": email, "password": password, "remember": remember}
    )
    assert res.status_code == 200
    header = res.headers["set-cookie"]
    return header, res.cookies["memchentic_session"]


def _refresh(client: TestClient, cookie: str, headers: dict[str, str] | None = None):
    return client.post(
        "/api/auth/refresh",
        headers={**(GUI if headers is None else headers), "Cookie": f"memchentic_session={cookie}"},
    )


def test_remember_me_cookie_is_persistent_and_locked_down(client: TestClient) -> None:
    header, _ = _login(client, remember=True)
    lowered = header.lower()
    assert "max-age=" in lowered
    assert "httponly" in lowered
    assert "samesite=strict" in lowered
    assert "path=/api/auth" in lowered


def test_browser_session_cookie_has_no_max_age(client: TestClient) -> None:
    header, _ = _login(client, remember=False)
    assert "max-age=" not in header.lower()


def test_refresh_mints_working_access_token(client: TestClient) -> None:
    _, cookie = _login(client, remember=True)
    client.cookies.clear()
    res = _refresh(client, cookie)
    assert res.status_code == 200
    token = res.json()["access_token"]
    me = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200


def test_refresh_requires_gui_header(client: TestClient) -> None:
    _, cookie = _login(client, remember=True)
    client.cookies.clear()
    assert _refresh(client, cookie, headers={}).status_code == 403


def test_logout_revokes_session(client: TestClient) -> None:
    _, cookie = _login(client, remember=True)
    client.cookies.clear()
    out = client.post("/api/auth/logout", headers={**GUI, "Cookie": f"memchentic_session={cookie}"})
    assert out.status_code == 200
    assert _refresh(client, cookie).status_code == 401


def test_refresh_is_rate_limited_per_client(client: TestClient) -> None:
    statuses = [
        client.post(
            "/api/auth/refresh",
            headers={**GUI, "Cookie": "memchentic_session=not-a-real-session"},
        ).status_code
        for _ in range(125)
    ]
    assert statuses[0] == 401
    assert statuses[119] == 401
    assert statuses[120] == 429
    assert statuses[-1] == 429


def test_admin_revoke_credentials_ends_sessions_and_keys(client: TestClient) -> None:
    """Revoke ends stored credentials and keeps the account."""
    email = f"rv{uuid.uuid4().hex[:10]}@example.com"
    password = f"pw-{uuid.uuid4().hex}"
    client.post("/api/auth/register", json={"email": email, "password": password})
    login = client.post(
        "/api/auth/login", json={"email": email, "password": password, "remember": True}
    )
    assert login.status_code == 200, login.text
    user_id = login.json()["id"]
    cookie = login.cookies["memchentic_session"]
    bearer = {"Authorization": f"Bearer {login.json()['access_token']}"}
    client.cookies.clear()
    api_key = client.post("/api/api-keys", headers=bearer, json={"name": "t"}).json()["key"]
    key_auth = {"Authorization": f"Bearer {api_key}"}
    assert client.get("/api/me", headers=key_auth).status_code == 200
    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}
    url = f"/api/users/{user_id}/revoke-credentials"

    assert client.post(url, headers=bearer).status_code == 403
    assert _refresh(client, cookie).status_code == 200
    client.cookies.clear()

    done = client.post(url, headers=admin)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "ok"
    assert done.json()["access_tokens_expire_within_seconds"] > 0

    assert _refresh(client, cookie).status_code == 401
    assert client.get("/api/me", headers=key_auth).status_code == 401
    unknown = client.post(f"/api/users/{uuid.uuid4()}/revoke-credentials", headers=admin)
    assert unknown.status_code == 404

    # The account is kept: the user can sign in again.
    again = client.post("/api/auth/login", json={"email": email, "password": password})
    assert again.status_code == 200
    client.cookies.clear()
