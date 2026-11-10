"""Baseline security response headers."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from memchentic.api.security_headers import SecurityHeadersMiddleware


def test_baseline_headers_on_api_response(client: TestClient) -> None:
    res = client.get("/api/health")
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["referrer-policy"] == "no-referrer"
    assert res.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in res.headers["content-security-policy"]
    assert "camera=()" in res.headers["permissions-policy"]


def test_baseline_headers_on_unauthenticated_mcp_response(client: TestClient) -> None:
    res = client.post("/mcp", json={})
    assert res.status_code == 401
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.headers["x-frame-options"] == "DENY"


async def _response_headers(*, hsts: bool, preset: list[tuple[bytes, bytes]]) -> list[Any]:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": list(preset)})
        await send({"type": "http.response.body", "body": b""})

    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await SecurityHeadersMiddleware(app, hsts=hsts)({"type": "http", "path": "/"}, receive, send)
    return sent[0]["headers"]


async def test_hsts_only_when_enabled_and_route_headers_win() -> None:
    with_hsts = dict(await _response_headers(hsts=True, preset=[]))
    assert with_hsts[b"strict-transport-security"] == b"max-age=31536000; includeSubDomains"

    without_hsts = dict(await _response_headers(hsts=False, preset=[]))
    assert b"strict-transport-security" not in without_hsts

    kept = await _response_headers(hsts=False, preset=[(b"x-frame-options", b"SAMEORIGIN")])
    assert [value for name, value in kept if name == b"x-frame-options"] == [b"SAMEORIGIN"]
