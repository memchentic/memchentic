"""Regression tests for the obvious-bug catalogue fixes."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import quote_plus

import pytest
from fastapi.testclient import TestClient

from memchentic.events.memory_bus import _connect_kwargs_from_settings
from memchentic.settings import get_settings

ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "")


def _user(client: TestClient) -> dict[str, str]:
    email = f"obv-{uuid.uuid4().hex[:10]}@example.com"
    password = f"pw-{uuid.uuid4().hex}"
    client.post("/api/auth/register", json={"email": email, "password": password})
    token = client.post(
        "/api/auth/login", json={"email": email, "password": password}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_blank_search_returns_empty_without_error(client: TestClient) -> None:
    auth = _user(client)
    resp = client.get("/api/memories/search", headers=auth, params={"q": "   "})
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_expired_near_dup_does_not_block_remember(client: TestClient) -> None:
    auth = _user(client)
    marker = uuid.uuid4().hex[:10]
    title = f"Expired twin {marker}"
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    first = client.post(
        "/api/memories",
        headers=auth,
        json={
            "title": title,
            "body": f"body {marker}",
            "type": "fact",
            "expires_at": past,
            "force": True,
        },
    )
    assert first.status_code == 200, first.text
    second = client.post(
        "/api/memories",
        headers=auth,
        json={"title": title, "body": f"body {marker} again", "type": "fact"},
    )
    assert second.status_code == 200, second.text
    assert second.json()["id"] != first.json()["id"]


def test_open_title_ambiguous_errors(client: TestClient) -> None:
    auth = _user(client)
    marker = uuid.uuid4().hex[:10]
    title = f"Same title {marker}"
    g1 = client.post("/api/spaces", headers=auth, json={"name": f"a-{marker}"}).json()
    g2 = client.post("/api/spaces", headers=auth, json={"name": f"b-{marker}"}).json()
    for space_id in (g1["id"], g2["id"]):
        resp = client.post(
            "/api/memories",
            headers=auth,
            json={
                "title": title,
                "body": f"x {marker}",
                "type": "fact",
                "space_id": space_id,
                "force": True,
            },
        )
        assert resp.status_code == 200, resp.text

    mcp = client.post(
        "/mcp",
        headers={**auth, "Accept": "application/json, text/event-stream"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "open", "arguments": {"title": title}},
        },
    )
    assert mcp.status_code == 200, mcp.text
    payload = mcp.json()
    # FastMCP surfaces tool ValueError as isError / text content.
    result = payload.get("result") or {}
    assert result.get("isError") is True or "matches" in str(payload).lower()


@pytest.mark.skipif(not ADMIN_KEY, reason="ADMIN_API_KEY unset")
def test_admin_personal_space_id_is_own(client: TestClient) -> None:
    """Admin me.personal_space_id must be the admin user's personal space."""
    # Seed another personal space that is newer than the admin's.
    _user(client)
    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}
    me = client.get("/api/me", headers=admin).json()
    assert me["is_admin"] is True
    assert me.get("personal_space_id")
    assert me.get("id")
    write = client.post(
        "/api/memories",
        headers=admin,
        json={
            "title": f"admin-own-{uuid.uuid4().hex[:8]}",
            "body": "owned",
            "type": "fact",
            "space_id": me["personal_space_id"],
            "force": True,
        },
    )
    assert write.status_code == 200, write.text
    assert write.json()["created_by"] == me["id"]
    assert write.json()["space_id"] == me["personal_space_id"]


def test_connect_kwargs_preserve_special_password(monkeypatch: pytest.MonkeyPatch) -> None:
    password = "p@ss/w:rd#1"
    monkeypatch.setenv("POSTGRES_APP_USER", "mem")
    monkeypatch.setenv("POSTGRES_APP_PASSWORD", password)
    monkeypatch.setenv("POSTGRES_HOSTNAME", "dbhost")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_DB", "memchentic")
    get_settings.cache_clear()
    try:
        # Settings quote_plus when building the URL; kwargs must unquote cleanly.
        assert quote_plus(password) in get_settings().database_url
        kwargs = _connect_kwargs_from_settings()
        assert kwargs["user"] == "mem"
        assert kwargs["password"] == password
        assert kwargs["host"] == "dbhost"
        assert kwargs["port"] == 5432
        assert kwargs["database"] == "memchentic"
    finally:
        get_settings.cache_clear()


def test_by_tag_mode_or_case_insensitive(client: TestClient) -> None:
    auth = _user(client)
    marker = uuid.uuid4().hex[:10]
    client.post(
        "/api/memories",
        headers=auth,
        json={
            "title": f"tagged {marker}",
            "body": "x",
            "type": "fact",
            "tags": [f"a-{marker}", f"b-{marker}"],
            "force": True,
        },
    )
    mcp = client.post(
        "/mcp",
        headers={**auth, "Accept": "application/json, text/event-stream"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "by_tag",
                "arguments": {"tags": [f"a-{marker}"], "mode": "OR"},
            },
        },
    )
    assert mcp.status_code == 200, mcp.text
    content = mcp.json()["result"]["structuredContent"]
    rows = content if isinstance(content, list) else content.get("result", content)
    assert any(d["title"] == f"tagged {marker}" for d in rows)


def test_health_exposes_embedder(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "memchentic"
    assert "embedder" in body
    assert body["status"] in {"ok", "degraded"}
