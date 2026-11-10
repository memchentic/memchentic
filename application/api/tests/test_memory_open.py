"""MCP open: lookup by id and by title."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from support import register_user


def _user(client: TestClient) -> dict[str, str]:
    return register_user(client).headers


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
