"""MCP requests: a failed tool call persists nothing; a successful one commits."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from memchentic.services import memories as memory_service
from support import call_tool, register_user

_MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def _user(client: TestClient) -> dict[str, str]:
    return register_user(client).headers


def _call(rpc_id: int, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": args},
    }


def _tool(
    client: TestClient, auth: dict[str, str], name: str, args: dict[str, Any]
) -> dict[str, Any]:
    """Call an MCP tool and return the JSON-RPC ``result`` (may carry ``isError``)."""
    return call_tool(client, auth, name, args)


def _create(client: TestClient, auth: dict[str, str]) -> dict[str, Any]:
    resp = client.post(
        "/api/memories",
        headers=auth,
        json={
            "title": f"orig {uuid.uuid4().hex[:8]}",
            "body": "body",
            "type": "fact",
            "force": True,
        },
    )
    assert resp.status_code == 200, resp.text
    created: dict[str, Any] = resp.json()
    return created


def test_failed_revise_leaves_the_memory_unchanged(client: TestClient) -> None:
    auth = _user(client)
    created = _create(client, auth)

    result = _tool(
        client,
        auth,
        "revise",
        {"id": created["id"], "title": "CHANGED BY FAILED CALL", "type": "bogus"},
    )

    assert result.get("isError") is True
    after = client.get(f"/api/memories/{created['id']}", headers=auth).json()
    assert after["title"] == created["title"]
    assert after["updated_at"] == created["updated_at"]


def test_tool_error_after_mutation_is_rolled_back(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The middleware itself must discard changes left by a failed tool call."""
    auth = _user(client)
    created = _create(client, auth)

    async def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise ValueError("event bus unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(memory_service, "publish_memory_event", _boom)
        result = _tool(
            client, auth, "revise", {"id": created["id"], "title": "CHANGED THEN FAILED"}
        )

    assert result.get("isError") is True
    after = client.get(f"/api/memories/{created['id']}", headers=auth).json()
    assert after["title"] == created["title"]


def test_successful_tool_calls_still_commit(client: TestClient) -> None:
    auth = _user(client)
    title = f"commit {uuid.uuid4().hex[:8]}"

    made = _tool(
        client, auth, "remember", {"title": title, "body": "v1", "type": "fact", "force": True}
    )["structuredContent"]
    opened = _tool(client, auth, "open", {"id": made["id"]})
    assert opened.get("isError") is not True
    rows = client.get("/api/memories", headers=auth, params={"limit": 100}).json()
    assert next(r for r in rows if r["id"] == made["id"])["recall_count"] == 1

    revised = _tool(client, auth, "revise", {"id": made["id"], "body": "v2"})
    assert revised.get("isError") is not True
    assert client.get(f"/api/memories/{made['id']}", headers=auth).json()["body"] == "v2"

    forgotten = _tool(client, auth, "forget", {"id": made["id"]})
    assert forgotten.get("isError") is not True
    assert client.get(f"/api/memories/{made['id']}", headers=auth).status_code == 404


def test_jsonrpc_batch_is_rejected_or_answered_correctly(client: TestClient) -> None:
    """OQ-036: a batch never runs two tool calls on one session concurrently."""
    auth = _user(client)
    title = f"batch {uuid.uuid4().hex[:8]}"
    batch = [
        _call(1, "remember", {"title": title, "body": "b", "type": "fact", "force": True}),
        _call(2, "find", {"query": title}),
    ]

    resp = client.post("/mcp", headers={**auth, **_MCP_HEADERS}, json=batch)

    body = resp.json() if resp.content else None
    if resp.status_code >= 400 or isinstance(body, dict):
        # Rejected as a whole (HTTP error or a single JSON-RPC error object).
        assert not isinstance(body, dict) or "result" not in body
        return
    assert isinstance(body, list)
    assert len(body) == 2
    for item in body:
        assert item["result"].get("isError") is not True, item
