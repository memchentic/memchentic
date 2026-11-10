"""Memory manage ACL: forget/relocate require creator, group manager, or admin."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient


def _user(client: TestClient) -> tuple[str, str, str]:
    """Register and log in a fresh internal user.

    Returns:
        Tuple of (access token, user id, email).
    """
    email = f"t{uuid.uuid4().hex[:10]}@example.com"
    password = f"pw-{uuid.uuid4().hex}"
    client.post("/api/auth/register", json={"email": email, "password": password})
    token = client.post("/api/auth/login", json={"email": email, "password": password}).json()[
        "access_token"
    ]
    user_id = client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
    return token, user_id, email


def _tool(client: TestClient, token: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call an MCP tool over streamable HTTP."""
    resp = client.post(
        "/mcp",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        },
    )
    resp.raise_for_status()
    return resp.json()["result"]


def _shared_group(
    client: TestClient,
) -> tuple[str, str, str, str, str]:
    """Create owner A, member B, and a shared group owned by A.

    Returns:
        ``(token_a, token_b, group_id, user_a, user_b)``.
    """
    token_a, user_a, _ = _user(client)
    token_b, user_b, _ = _user(client)
    auth_a = {"Authorization": f"Bearer {token_a}"}
    group = client.post(
        "/api/spaces", headers=auth_a, json={"name": f"acl-{uuid.uuid4().hex[:8]}"}
    ).json()
    assert (
        client.post(
            f"/api/spaces/{group['id']}/members",
            headers=auth_a,
            json={"user_id": user_b},
        ).status_code
        == 200
    )
    return token_a, token_b, group["id"], user_a, user_b


def test_member_cannot_forget_another_members_memory(client: TestClient) -> None:
    """Shared-group member cannot forget a memory they did not create."""
    token_a, token_b, group_id, _, _ = _shared_group(client)
    marker = uuid.uuid4().hex
    memory_id = _tool(
        client,
        token_a,
        "remember",
        {"title": f"owner-entry {marker}", "body": marker, "space_id": group_id},
    )["structuredContent"]["id"]

    refused = _tool(client, token_b, "forget", {"id": memory_id})
    assert refused.get("isError") is True

    still = _tool(client, token_a, "open", {"id": memory_id})
    assert still["structuredContent"]["id"] == memory_id


def test_creator_can_forget_own_memory_in_shared_group(client: TestClient) -> None:
    """Creator may forget their own shared-group memory."""
    _, token_b, group_id, _, _ = _shared_group(client)
    marker = uuid.uuid4().hex
    memory_id = _tool(
        client,
        token_b,
        "remember",
        {"title": f"member-entry {marker}", "body": marker, "space_id": group_id},
    )["structuredContent"]["id"]
    forgotten = _tool(client, token_b, "forget", {"id": memory_id})
    assert forgotten.get("isError") is not True
    missing = _tool(client, token_b, "open", {"id": memory_id})
    assert missing.get("isError") is True


def test_group_manager_can_forget_member_memory(client: TestClient) -> None:
    """Group creator (manager) may forget another member's memory."""
    token_a, token_b, group_id, _, _ = _shared_group(client)
    marker = uuid.uuid4().hex
    memory_id = _tool(
        client,
        token_b,
        "remember",
        {"title": f"member-entry {marker}", "body": marker, "space_id": group_id},
    )["structuredContent"]["id"]
    forgotten = _tool(client, token_a, "forget", {"id": memory_id})
    assert forgotten.get("isError") is not True
    missing = _tool(client, token_a, "open", {"id": memory_id})
    assert missing.get("isError") is True


def test_member_cannot_relocate_another_members_memory(client: TestClient) -> None:
    """Shared-group member cannot relocate a memory they did not create."""
    token_a, token_b, group_id, _, user_b = _shared_group(client)
    auth_a = {"Authorization": f"Bearer {token_a}"}
    marker = uuid.uuid4().hex
    memory_id = _tool(
        client,
        token_a,
        "remember",
        {"title": f"owner-entry {marker}", "body": marker, "space_id": group_id},
    )["structuredContent"]["id"]
    other = client.post(
        "/api/spaces", headers=auth_a, json={"name": f"other-{uuid.uuid4().hex[:8]}"}
    ).json()
    assert (
        client.post(
            f"/api/spaces/{other['id']}/members",
            headers=auth_a,
            json={"user_id": user_b},
        ).status_code
        == 200
    )

    refused = _tool(client, token_b, "relocate", {"id": memory_id, "space_id": other["id"]})
    assert refused.get("isError") is True
    opened = _tool(client, token_a, "open", {"id": memory_id})
    assert opened["structuredContent"]["space_id"] == group_id


def test_creator_and_manager_can_relocate(client: TestClient) -> None:
    """Creator and group manager may relocate; peer member may not."""
    token_a, token_b, group_id, _, user_b = _shared_group(client)
    token_c, user_c, _ = _user(client)
    auth_a = {"Authorization": f"Bearer {token_a}"}
    assert (
        client.post(
            f"/api/spaces/{group_id}/members",
            headers=auth_a,
            json={"user_id": user_c},
        ).status_code
        == 200
    )
    dest = client.post(
        "/api/spaces", headers=auth_a, json={"name": f"dest-{uuid.uuid4().hex[:8]}"}
    ).json()
    for uid in (user_b, user_c):
        assert (
            client.post(
                f"/api/spaces/{dest['id']}/members",
                headers=auth_a,
                json={"user_id": uid},
            ).status_code
            == 200
        )

    marker = uuid.uuid4().hex
    by_b = _tool(
        client,
        token_b,
        "remember",
        {"title": f"b-entry {marker}", "body": marker, "space_id": group_id},
    )["structuredContent"]["id"]
    by_c = _tool(
        client,
        token_c,
        "remember",
        {"title": f"c-entry {marker}", "body": marker, "space_id": group_id},
    )["structuredContent"]["id"]

    assert (
        _tool(client, token_c, "relocate", {"id": by_b, "space_id": dest["id"]}).get("isError")
        is True
    )
    moved = _tool(client, token_b, "relocate", {"id": by_b, "space_id": dest["id"]})
    assert moved.get("isError") is not True
    assert moved["structuredContent"]["space_id"] == dest["id"]
    moved_c = _tool(client, token_a, "relocate", {"id": by_c, "space_id": dest["id"]})
    assert moved_c.get("isError") is not True
    assert moved_c["structuredContent"]["space_id"] == dest["id"]


def test_member_can_still_revise_peer_visible_memory(client: TestClient) -> None:
    """revise stays member-level (open_memory only), not manage ACL."""
    token_a, token_b, group_id, _, _ = _shared_group(client)
    marker = uuid.uuid4().hex
    memory_id = _tool(
        client,
        token_a,
        "remember",
        {"title": f"owner-entry {marker}", "body": marker, "space_id": group_id},
    )["structuredContent"]["id"]
    revised = _tool(
        client,
        token_b,
        "revise",
        {"id": memory_id, "body": f"updated {marker}"},
    )
    assert revised.get("isError") is not True
    assert revised["structuredContent"]["body"] == f"updated {marker}"
