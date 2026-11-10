"""MCP endpoint: authentication challenge and tool list."""

from __future__ import annotations

from fastapi.testclient import TestClient

from support import register_user


def _user(client: TestClient) -> tuple[str, str, str]:
    """Register and log in a fresh internal user.

    Args:
        client: In-process test client.

    Returns:
        Tuple of (access token, user id, email).
    """
    user = register_user(client)
    return user.token, user.user_id, user.email


def test_unauthenticated_mcp_gets_resource_metadata_challenge(client: TestClient) -> None:
    """MCP without credentials returns 401 pointing at protected resource metadata."""
    resp = client.post("/mcp", json={})
    assert resp.status_code == 401
    assert "resource_metadata=" in resp.headers["www-authenticate"]


def test_tool_surface_is_memchentic_native(client: TestClient) -> None:
    """The MCP server exposes exactly the documented v1 tool names."""
    token, _, _ = _user(client)
    resp = client.post(
        "/mcp",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    names = {t["name"] for t in resp.json()["result"]["tools"]}
    assert names == {
        "me",
        "space_list",
        "space_create",
        "space_show",
        "remember",
        "find",
        "open",
        "revise",
        "forget",
        "browse",
        "since",
        "by_tag",
        "relocate",
        "guide_write",
        "guide_find",
        "about_path",
        "trace",
        "overlap_report",
    }
