"""MCP read tools: result shape and access control."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from support import RegisteredUser, add_group_member, create_group, tool_rows

McpTool = Callable[[dict[str, str], str, dict[str, Any]], dict[str, Any]]


@pytest.fixture
def seeded(client: TestClient, make_user: Callable[..., RegisteredUser]) -> SimpleNamespace:
    """An owner with one entry and one guide in the personal space, and a stranger."""
    owner = make_user()
    stranger = make_user()
    marker = uuid.uuid4().hex[:12]
    entry = client.post(
        "/api/memories",
        headers=owner.headers,
        json={
            "title": f"entry {marker}",
            "body": f"entry body {marker}",
            "type": "fact",
            "path_hint": f"src/{marker}/module.py",
            "force": True,
        },
    )
    assert entry.status_code == 200, entry.text
    guide = client.post(
        "/api/guides",
        headers=owner.headers,
        json={"title": f"guide {marker}", "body": f"when {marker} happens, do the thing"},
    )
    assert guide.status_code == 200, guide.text
    return SimpleNamespace(
        owner=owner,
        stranger=stranger,
        marker=marker,
        entry_id=entry.json()["id"],
        guide_id=guide.json()["id"],
    )


def _ids(result: dict[str, Any]) -> set[str]:
    return {row["id"] for row in tool_rows(result)}


def test_since_returns_only_accessible_rows(seeded: SimpleNamespace, mcp_tool: McpTool) -> None:
    start = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    own = _ids(mcp_tool(seeded.owner.headers, "since", {"since": start}))
    assert seeded.entry_id in own
    assert seeded.guide_id in own
    other = _ids(mcp_tool(seeded.stranger.headers, "since", {"since": start}))
    assert seeded.entry_id not in other
    assert seeded.guide_id not in other


def test_browse_returns_only_accessible_rows(seeded: SimpleNamespace, mcp_tool: McpTool) -> None:
    own = _ids(mcp_tool(seeded.owner.headers, "browse", {}))
    assert {seeded.entry_id, seeded.guide_id} <= own
    other = _ids(mcp_tool(seeded.stranger.headers, "browse", {}))
    assert not {seeded.entry_id, seeded.guide_id} & other


def test_about_path_matches_path_hint_for_owner_only(
    seeded: SimpleNamespace, mcp_tool: McpTool
) -> None:
    args = {"path": f"src/{seeded.marker}"}
    assert _ids(mcp_tool(seeded.owner.headers, "about_path", args)) == {seeded.entry_id}
    assert _ids(mcp_tool(seeded.stranger.headers, "about_path", args)) == set()


def test_guide_find_returns_guides_for_owner_only(
    seeded: SimpleNamespace, mcp_tool: McpTool
) -> None:
    args = {"query": f"guide {seeded.marker}"}
    rows = tool_rows(mcp_tool(seeded.owner.headers, "guide_find", args))
    assert seeded.guide_id in {row["id"] for row in rows}
    assert {row["kind"] for row in rows} == {"guide"}
    assert seeded.guide_id not in _ids(mcp_tool(seeded.stranger.headers, "guide_find", args))


def test_trace_shape_and_access(seeded: SimpleNamespace, mcp_tool: McpTool) -> None:
    result = mcp_tool(seeded.owner.headers, "trace", {"id": seeded.entry_id})
    assert result.get("isError") is not True
    traced = result["structuredContent"]
    assert set(traced) >= {
        "id",
        "title",
        "kind",
        "created_by",
        "created_at",
        "provenance",
        "space_id",
    }
    assert traced["id"] == seeded.entry_id
    assert traced["created_by"] == seeded.owner.user_id

    refused = mcp_tool(seeded.stranger.headers, "trace", {"id": seeded.entry_id})
    assert refused.get("isError") is True


def test_space_list_and_space_show_respect_membership(
    client: TestClient, make_user: Callable[..., RegisteredUser], mcp_tool: McpTool
) -> None:
    owner = make_user()
    member = make_user()
    stranger = make_user()
    group_id = create_group(client, owner.headers)
    add_group_member(client, owner.headers, group_id, member)

    member_spaces = tool_rows(mcp_tool(member.headers, "space_list", {}))
    assert group_id in {space["id"] for space in member_spaces}
    assert [space["kind"] for space in member_spaces].count("personal") == 1
    stranger_spaces = tool_rows(mcp_tool(stranger.headers, "space_list", {}))
    assert group_id not in {space["id"] for space in stranger_spaces}

    shown = mcp_tool(member.headers, "space_show", {"space_id": group_id})["structuredContent"]
    assert shown["id"] == group_id
    assert shown["created_by"] == owner.user_id
    assert {m["user_id"] for m in shown["members"]} == {owner.user_id, member.user_id}

    refused = mcp_tool(stranger.headers, "space_show", {"space_id": group_id})
    assert refused.get("isError") is True
