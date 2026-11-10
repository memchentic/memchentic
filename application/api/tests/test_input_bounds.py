"""Length, count and range bounds on REST and MCP input (SEC-052)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from memchentic.services import limits
from memchentic.services.errors import ValidationError
from memchentic.services.validation import (
    check_offset,
    check_tags,
    check_text,
    check_title,
    check_ttl_days,
)

PREFIX = "ib"


def _user(client: TestClient) -> dict[str, str]:
    """Register and sign in a fresh user; return the auth headers."""
    email = f"{PREFIX}{uuid.uuid4().hex[:10]}@example.com"
    password = f"pw-{uuid.uuid4().hex}"
    assert (
        client.post("/api/auth/register", json={"email": email, "password": password}).status_code
        == 200
    )
    token = client.post("/api/auth/login", json={"email": email, "password": password}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {token}"}


def _tool(client: TestClient, auth: dict[str, str], name: str, args: dict[str, Any]) -> dict:
    """Call an MCP tool; return the JSON-RPC ``result`` object."""
    resp = client.post(
        "/mcp",
        headers={**auth, "Accept": "application/json, text/event-stream"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        },
    )
    resp.raise_for_status()
    return resp.json()["result"]


def _memory(**extra: Any) -> dict[str, Any]:
    return {"title": f"b {uuid.uuid4().hex}", "body": "b", "force": True, **extra}


def test_validators_enforce_the_limits() -> None:
    assert check_title("  ok  ") == "ok"
    assert check_ttl_days(limits.TTL_DAYS_MAX) == limits.TTL_DAYS_MAX
    assert check_offset(limits.OFFSET_MAX) == limits.OFFSET_MAX
    assert check_tags(None) == []
    for call in (
        lambda: check_title("x" * (limits.TITLE_MAX + 1)),
        lambda: check_text("a\x00b", "body", limits.BODY_MAX),
        lambda: check_tags(["t"] * (limits.TAG_MAX_COUNT + 1)),
        lambda: check_tags(["t" * (limits.TAG_MAX_LEN + 1)]),
        lambda: check_ttl_days(limits.TTL_DAYS_MAX + 1),
        lambda: check_ttl_days(-1),
        lambda: check_offset(-1),
        lambda: check_offset(limits.OFFSET_MAX + 1),
    ):
        with pytest.raises(ValidationError):
            call()


def test_rest_rejects_oversize_memory_fields(client: TestClient) -> None:
    auth = _user(client)
    cases = [
        _memory(title="x" * (limits.TITLE_MAX + 1)),
        _memory(path_hint="p" * (limits.PATH_HINT_MAX + 1)),
        _memory(repo_hint="r" * (limits.REPO_HINT_MAX + 1)),
        _memory(tags=["t"] * (limits.TAG_MAX_COUNT + 1)),
        _memory(tags=["t" * (limits.TAG_MAX_LEN + 1)]),
        _memory(ttl_days=limits.TTL_DAYS_MAX + 1),
        _memory(ttl_days=10**9),
        _memory(body="b" * (limits.BODY_MAX + 1)),
    ]
    for payload in cases:
        resp = client.post("/api/memories", headers=auth, json=payload)
        assert resp.status_code == 422, (list(payload), resp.status_code, resp.text[:200])


def test_rest_accepts_values_at_the_limit(client: TestClient) -> None:
    auth = _user(client)
    resp = client.post(
        "/api/memories",
        headers=auth,
        json=_memory(
            title="x" * limits.TITLE_MAX,
            path_hint="p" * limits.PATH_HINT_MAX,
            tags=[f"t{i}" for i in range(limits.TAG_MAX_COUNT)],
            ttl_days=limits.TTL_DAYS_MAX,
        ),
    )
    assert resp.status_code == 200, resp.text
    assert (
        client.get("/api/memories", headers=auth, params={"offset": limits.OFFSET_MAX}).status_code
        == 200
    )


def test_rest_rejects_oversize_names(client: TestClient) -> None:
    auth = _user(client)
    space = {"name": "s" * (limits.SPACE_NAME_MAX + 1)}
    assert client.post("/api/spaces", headers=auth, json=space).status_code == 422
    key = {"name": "k" * (limits.API_KEY_NAME_MAX + 1)}
    assert client.post("/api/api-keys", headers=auth, json=key).status_code == 422
    resp = client.post(
        "/api/auth/register",
        json={
            "email": f"d{uuid.uuid4().hex[:8]}@example.com",
            "password": f"pw-{uuid.uuid4().hex}",
            "display_name": "d" * (limits.DISPLAY_NAME_MAX + 1),
        },
    )
    assert resp.status_code == 422, resp.text


def test_rest_pagination_bounds(client: TestClient) -> None:
    auth = _user(client)
    for params in ({"offset": -1}, {"offset": limits.OFFSET_MAX + 1}):
        assert client.get("/api/memories", headers=auth, params=params).status_code == 422
        searched = client.get("/api/memories/search", headers=auth, params={"q": "x", **params})
        assert searched.status_code == 422
    long_query = {"q": "q" * (limits.QUERY_MAX + 1)}
    assert client.get("/api/memories/search", headers=auth, params=long_query).status_code == 422
    # limit is clamped by the service, never an error.
    for limit in (0, -5, 10**6):
        assert client.get("/api/memories", headers=auth, params={"limit": limit}).status_code == 200


def test_mcp_tools_enforce_the_same_bounds(client: TestClient) -> None:
    auth = _user(client)
    too_many_tags = ["t"] * (limits.TAG_MAX_COUNT + 1)
    calls: list[tuple[str, dict[str, Any]]] = [
        ("remember", {"title": "x" * (limits.TITLE_MAX + 1), "body": "b", "force": True}),
        ("remember", {"title": "t", "body": "b", "ttl_days": 10**9, "force": True}),
        ("remember", {"title": "t", "body": "b", "force": True, "tags": too_many_tags}),
        ("guide_write", {"title": "x" * (limits.TITLE_MAX + 1), "body": "b"}),
        ("space_create", {"name": "s" * (limits.SPACE_NAME_MAX + 1)}),
        ("browse", {"offset": -1}),
        ("find", {"query": "q" * (limits.QUERY_MAX + 1)}),
    ]
    for name, args in calls:
        result = _tool(client, auth, name, args)
        assert result.get("isError") is True, (name, result)
        assert "invalid: " in result["content"][0]["text"], (name, result)
