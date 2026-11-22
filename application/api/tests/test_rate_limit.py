"""Login throttling, shared Postgres rate limits and the client address."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.types import Receive, Scope, Send
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from memchentic.auth.rate_limit import client_ip


def _login(client: TestClient, email: str) -> int:
    resp = client.post("/api/auth/login", json={"email": email, "password": "wrong-password"})
    return resp.status_code


@pytest.fixture
def fast_password_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip bcrypt: these tests count requests, not password strength."""
    monkeypatch.setattr("memchentic.auth.users.verify_password", lambda *_a, **_k: False)


def test_login_is_capped_per_client_across_emails(
    client: TestClient, fast_password_check: None
) -> None:
    """Rotating the email does not reset the throttle (shared Postgres counter)."""
    codes = [_login(client, f"spray{i}-{uuid.uuid4().hex[:6]}@example.com") for i in range(61)]
    assert codes[:60] == [401] * 60
    assert codes[60] == 429


def test_login_bucket_ignores_surrounding_whitespace(
    client: TestClient, fast_password_check: None
) -> None:
    """Padding the email with spaces reaches the same bucket as the account lookup."""
    email = f"pad-{uuid.uuid4().hex[:8]}@example.com"
    assert [_login(client, email) for _ in range(20)] == [401] * 20
    assert _login(client, f"  {email} ") == 429
    assert _login(client, email.upper()) == 429


def test_login_bucket_key_has_a_fixed_size(client: TestClient, fast_password_check: None) -> None:
    """An oversized email still throttles through the shared limiter."""
    email = f"{'k' * 300}@example.com"
    assert _login(client, email) == 401
    codes = [_login(client, email) for _ in range(20)]
    assert 429 in codes or _login(client, email) == 429


def test_login_rejects_oversized_fields(client: TestClient) -> None:
    """Unbounded text never reaches the limiter or bcrypt."""
    long_email = "a" * 10_000 + "@example.com"
    resp = client.post("/api/auth/login", json={"email": long_email, "password": "x"})
    assert resp.status_code == 422
    resp = client.post("/api/auth/login", json={"email": "a@example.com", "password": "p" * 5_000})
    assert resp.status_code == 422
    resp = client.post("/api/auth/login", json={"email": "", "password": "x"})
    assert resp.status_code == 422


def test_oauth_token_rate_limit_is_shared(client: TestClient) -> None:
    """Token endpoint uses the Postgres limiter (20 / minute per client)."""
    codes = [
        client.post(
            "/oauth/token",
            data={"grant_type": "refresh_token", "refresh_token": "x"},
        ).status_code
        for _ in range(21)
    ]
    assert 429 in codes


def _scope(peer: str | None, forwarded_for: str | None = None) -> dict[str, Any]:
    headers = []
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode("latin1")))
    return {
        "type": "http",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "query_string": b"",
        "headers": headers,
        "client": (peer, 40000) if peer else None,
        "server": ("testserver", 80),
    }


def test_client_ip_reads_the_asgi_client() -> None:
    """The helper trusts only what the server put in the ASGI scope."""
    request = Request(_scope("203.0.113.7", forwarded_for="198.51.100.1"))
    assert client_ip(request) == "203.0.113.7"
    assert client_ip(Request(_scope(None))) == "unknown"


async def test_forwarded_for_is_honoured_only_from_a_trusted_peer() -> None:
    """FORWARDED_ALLOW_IPS semantics: only a trusted proxy may set the client."""
    seen: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(client_ip(Request(scope)))

    async def receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        del message

    wrapped = ProxyHeadersMiddleware(app, trusted_hosts="10.0.0.0/8")
    await wrapped(_scope("10.1.2.3", forwarded_for="198.51.100.1"), receive, send)
    await wrapped(_scope("203.0.113.7", forwarded_for="198.51.100.1"), receive, send)
    assert seen == ["198.51.100.1", "203.0.113.7"]
