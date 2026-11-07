"""Live tests for browser sessions (remember me) against a running api.

Skipped when the api is unreachable. Override the target with ``MEMCHENTIC_TEST_URL``.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

BASE = os.environ.get("MEMCHENTIC_TEST_URL", "http://127.0.0.1:8080")
GUI = {"X-Memchentic-Client": "gui"}


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


def _login(client: httpx.Client, *, remember: bool) -> tuple[str, str]:
    """Register a fresh user and log in.

    Args:
        client: HTTP client bound to the api.
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


def _refresh(client: httpx.Client, cookie: str, headers: dict[str, str] | None = None):
    return client.post(
        "/api/auth/refresh",
        headers={**(GUI if headers is None else headers), "Cookie": f"memchentic_session={cookie}"},
    )


def test_remember_me_cookie_is_persistent_and_locked_down() -> None:
    with httpx.Client(base_url=BASE, timeout=10) as client:
        header, _ = _login(client, remember=True)
    lowered = header.lower()
    assert "max-age=" in lowered
    assert "httponly" in lowered
    assert "samesite=strict" in lowered
    assert "path=/api/auth" in lowered


def test_browser_session_cookie_has_no_max_age() -> None:
    with httpx.Client(base_url=BASE, timeout=10) as client:
        header, _ = _login(client, remember=False)
    assert "max-age=" not in header.lower()


def test_refresh_mints_working_access_token() -> None:
    with httpx.Client(base_url=BASE, timeout=10) as client:
        _, cookie = _login(client, remember=True)
        client.cookies.clear()
        res = _refresh(client, cookie)
        assert res.status_code == 200
        token = res.json()["access_token"]
        me = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200


def test_refresh_requires_gui_header() -> None:
    with httpx.Client(base_url=BASE, timeout=10) as client:
        _, cookie = _login(client, remember=True)
        client.cookies.clear()
        assert _refresh(client, cookie, headers={}).status_code == 403


def test_logout_revokes_session() -> None:
    with httpx.Client(base_url=BASE, timeout=10) as client:
        _, cookie = _login(client, remember=True)
        client.cookies.clear()
        out = client.post(
            "/api/auth/logout", headers={**GUI, "Cookie": f"memchentic_session={cookie}"}
        )
        assert out.status_code == 200
        assert _refresh(client, cookie).status_code == 401
