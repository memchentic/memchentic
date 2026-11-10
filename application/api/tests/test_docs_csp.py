"""The docs pages get their own CSP; everything else keeps the default (SEC-055)."""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from memchentic.api.security_headers import DEFAULT_CSP, DOCS_CSP, SecurityHeadersMiddleware


async def _ok(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


async def _own_csp(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok", headers={"Content-Security-Policy": "default-src 'none'"})


def _client(docs_paths: tuple[str, ...]) -> TestClient:
    inner = Starlette(routes=[Route("/docs/own", _own_csp), Route("/{path:path}", _ok)])
    return TestClient(SecurityHeadersMiddleware(inner, hsts=False, docs_paths=docs_paths))


def _csp(client: TestClient, path: str) -> str:
    return client.get(path).headers["content-security-policy"]


def test_default_policy_is_unchanged() -> None:
    assert DEFAULT_CSP.decode() == (
        "default-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )


def test_docs_disabled_sends_default_policy_everywhere() -> None:
    client = _client(())
    for path in ("/", "/api/health", "/docs", "/redoc"):
        assert _csp(client, path) == DEFAULT_CSP.decode(), path


def test_docs_enabled_scopes_the_docs_policy_to_docs_paths() -> None:
    client = _client(("/docs", "/redoc"))
    for path in ("/docs", "/docs/oauth2-redirect", "/redoc"):
        assert _csp(client, path) == DOCS_CSP.decode(), path
    for path in ("/", "/api/health", "/openapi.json", "/docsx", "/api/docs", "/mcp"):
        assert _csp(client, path) == DEFAULT_CSP.decode(), path


def test_docs_policy_keeps_the_framing_and_form_rules() -> None:
    policy = DOCS_CSP.decode()
    for directive in ("frame-ancestors 'none'", "base-uri 'self'", "form-action 'self'"):
        assert directive in policy
    assert "*" not in policy
    resp = _client(("/docs",)).get("/docs")
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_route_set_policy_still_wins_on_a_docs_path() -> None:
    assert _csp(_client(("/docs",)), "/docs/own") == "default-src 'none'"


def test_spa_document_loads_nothing_from_another_origin(client: TestClient) -> None:
    resp = client.get("/")
    if resp.status_code == 404:
        pytest.skip("SPA bundle is not built in this environment")
    assert "fonts.googleapis.com" not in resp.text
    assert "fonts.gstatic.com" not in resp.text
    assert resp.headers["content-security-policy"] == DEFAULT_CSP.decode()
