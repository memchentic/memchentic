"""HTTP session tests (remember me) against the in-process app / memchentic_test."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

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
