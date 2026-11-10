"""One mapping from service errors to HTTP statuses and MCP error text (SEC-051)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from memchentic.services.errors import (
    ConflictError,
    ForbiddenError,
    NearDuplicateError,
    NotFoundError,
    ServiceError,
    ValidationError,
    http_error,
    tool_error_text,
)
from support import call_tool, register_user

PREFIX = "em"


def _user(client: TestClient) -> tuple[dict[str, str], str]:
    """Register and sign in a fresh user; return (auth headers, email)."""
    user = register_user(client)
    return user.headers, user.email


def _tool(client: TestClient, auth: dict[str, str], name: str, args: dict[str, Any]) -> dict:
    """Call an MCP tool; return the JSON-RPC ``result`` object."""
    return call_tool(client, auth, name, args)


def _error_text(result: dict) -> str:
    assert result.get("isError") is True, result
    return result["content"][0]["text"]


def _group_with_member(client: TestClient) -> tuple[dict[str, str], dict[str, str], str]:
    """Create a group owned by one user with a second user as member."""
    owner, _ = _user(client)
    member, member_email = _user(client)
    group = client.post("/api/spaces", headers=owner, json={"name": f"G {uuid.uuid4().hex[:8]}"})
    assert group.status_code == 200, group.text
    group_id = group.json()["id"]
    added = client.post(
        f"/api/spaces/{group_id}/members", headers=owner, json={"email": member_email}
    )
    assert added.status_code == 200, added.text
    accepted = client.post(f"/api/spaces/{group_id}/accept", headers=member)
    assert accepted.status_code == 200, accepted.text
    return owner, member, group_id


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (ValidationError("bad"), 422, "invalid"),
        (NotFoundError("gone"), 404, "not_found"),
        (ForbiddenError("no"), 403, "forbidden"),
        (ConflictError("clash"), 409, "conflict"),
        (NearDuplicateError("dup", duplicate_id="abc"), 409, "duplicate"),
    ],
)
def test_each_error_class_has_one_status_and_code(
    exc: ServiceError, status: int, code: str
) -> None:
    got_status, body = http_error(exc)
    assert got_status == status
    assert body["code"] == code
    assert body["detail"] == str(exc)
    assert tool_error_text(exc) == f"{code}: {exc}"


def test_typed_errors_keep_their_builtin_base() -> None:
    assert isinstance(ValidationError("x"), ValueError)
    assert isinstance(NotFoundError("x"), LookupError)
    assert isinstance(ForbiddenError("x"), PermissionError)
    assert isinstance(NearDuplicateError("x"), ValueError)
    assert http_error(NearDuplicateError("x", duplicate_id="abc"))[1]["duplicate_id"] == "abc"
    assert "duplicate_id" not in http_error(NearDuplicateError("x"))[1]


def test_malformed_ids_are_422_on_every_route(client: TestClient) -> None:
    auth, _ = _user(client)
    cases: list[tuple[str, str, dict[str, Any] | None]] = [
        ("GET", "/api/users/nope", None),
        ("DELETE", "/api/users/nope", None),
        ("GET", "/api/spaces/nope", None),
        ("PATCH", "/api/spaces/nope", {"name": "x"}),
        ("DELETE", "/api/spaces/nope", None),
        ("POST", "/api/spaces/nope/members", {"email": "a@example.com"}),
        ("DELETE", "/api/spaces/nope/members/nope", None),
        ("GET", "/api/memories/nope", None),
        ("PATCH", "/api/memories/nope", {"title": "x"}),
        ("DELETE", "/api/memories/nope", None),
        ("DELETE", "/api/api-keys/nope", None),
        ("PATCH", "/api/me", {"default_shared_space_id": "nope"}),
        ("POST", "/api/memories", {"title": "t", "body": "b", "space_id": "nope"}),
        ("POST", "/api/guides", {"title": "t", "body": "b", "space_id": "nope"}),
        ("POST", "/api/api-keys", {"user_id": "nope"}),
        ("POST", "/api/api-keys/cycle", {"user_id": "nope"}),
        ("GET", "/api/memories?space_id=nope", None),
        ("GET", "/api/memories/search?q=x&space_id=nope", None),
    ]
    for method, path, payload in cases:
        resp = client.request(method, path, headers=auth, json=payload)
        assert resp.status_code == 422, (method, path, resp.status_code, resp.text)


def test_invalid_values_on_create_are_422_not_409(client: TestClient) -> None:
    auth, _ = _user(client)
    for extra in ({"type": "bogus"}, {"expires_at": "garbage"}):
        resp = client.post("/api/memories", headers=auth, json={"title": "t", "body": "b", **extra})
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "invalid"
    resp = client.patch(
        f"/api/memories/{uuid.uuid4()}", headers=auth, json={"expires_at": "garbage"}
    )
    assert resp.status_code == 422, resp.text


def test_missing_resources_are_404(client: TestClient) -> None:
    auth, _ = _user(client)
    missing = str(uuid.uuid4())
    cases: list[tuple[str, str, dict[str, Any] | None]] = [
        ("PATCH", f"/api/memories/{missing}", {"title": "x"}),
        ("DELETE", f"/api/memories/{missing}", None),
        ("DELETE", f"/api/api-keys/{missing}", None),
        ("PATCH", f"/api/spaces/{missing}", {"name": "x"}),
        ("DELETE", f"/api/spaces/{missing}", None),
        ("POST", f"/api/spaces/{missing}/members", {"email": "a@example.com"}),
        ("DELETE", f"/api/spaces/{missing}/members/{missing}", None),
        ("POST", "/api/memories", {"title": "t", "body": "b", "space_id": missing}),
        ("POST", "/api/guides", {"title": "t", "body": "b", "space_id": missing}),
        ("PATCH", "/api/me", {"default_shared_space_id": missing}),
    ]
    for method, path, payload in cases:
        resp = client.request(method, path, headers=auth, json=payload)
        assert resp.status_code == 404, (method, path, resp.status_code, resp.text)
        assert resp.json()["code"] == "not_found"


def test_visible_but_not_allowed_stays_403(client: TestClient) -> None:
    _, member, group_id = _group_with_member(client)
    renamed = client.patch(f"/api/spaces/{group_id}", headers=member, json={"name": "mine now"})
    assert renamed.status_code == 403, renamed.text
    assert renamed.json()["code"] == "forbidden"
    deleted = client.delete(f"/api/spaces/{group_id}", headers=member)
    assert deleted.status_code == 403, deleted.text


def test_near_duplicate_is_409_with_duplicate_id(client: TestClient) -> None:
    auth, _ = _user(client)
    payload = {"title": f"dup {uuid.uuid4().hex}", "body": "the same body text", "type": "fact"}
    first = client.post("/api/memories", headers=auth, json=payload)
    assert first.status_code == 200, first.text
    second = client.post("/api/memories", headers=auth, json=payload)
    assert second.status_code == 409, second.text
    assert second.json()["code"] == "duplicate"
    assert second.json()["duplicate_id"] == first.json()["id"]


def test_another_users_api_key_is_404(client: TestClient) -> None:
    owner, _ = _user(client)
    other, _ = _user(client)
    key = client.post("/api/api-keys", headers=owner, json={"name": "k"})
    assert key.status_code == 200, key.text
    resp = client.delete(f"/api/api-keys/{key.json()['id']}", headers=other)
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "Key not found"


def test_mcp_errors_carry_the_code_prefix(client: TestClient) -> None:
    owner, member, group_id = _group_with_member(client)
    missing = _tool(client, owner, "revise", {"id": str(uuid.uuid4()), "title": "x"})
    assert "not_found: " in _error_text(missing)
    malformed = _tool(client, owner, "open", {"id": "nope"})
    assert "invalid: " in _error_text(malformed)

    args = {
        "title": f"mcp dup {uuid.uuid4().hex}",
        "body": "the same body text",
        "space_id": group_id,
    }
    created = _tool(client, owner, "remember", args)
    assert created.get("isError") is not True, created
    again = _tool(client, owner, "remember", args)
    assert "duplicate: " in _error_text(again)

    refused = _tool(client, member, "forget", {"id": created["structuredContent"]["id"]})
    assert "forbidden: " in _error_text(refused)


def test_tool_schema_is_unchanged_by_the_error_decorator(client: TestClient) -> None:
    auth, _ = _user(client)
    resp = client.post(
        "/mcp",
        headers={**auth, "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    resp.raise_for_status()
    tools = {t["name"]: t for t in resp.json()["result"]["tools"]}
    props = tools["remember"]["inputSchema"]["properties"]
    assert {"title", "body", "type", "tags", "space_id", "expires_at", "force"} <= set(props)
    assert "args" not in props
    assert "kwargs" not in props
