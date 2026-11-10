"""Browse/search filters for space scope and multi-type selection."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any, get_args

import pytest
from fastapi.testclient import TestClient

from support import RegisteredUser, register_user, tool_rows


def _user(client: TestClient) -> tuple[str, dict]:
    user = register_user(client)
    return user.token, client.get("/api/me", headers=user.headers).json()


def test_browse_space_scope_and_types(client: TestClient) -> None:
    """space_scope and types narrow browse results without client-side filtering."""
    token, me = _user(client)
    auth = {"Authorization": f"Bearer {token}"}
    marker = uuid.uuid4().hex[:8]

    group = client.post("/api/spaces", headers=auth, json={"name": f"g-{marker}"}).json()

    personal_fact = client.post(
        "/api/memories",
        headers=auth,
        json={
            "title": f"personal fact {marker}",
            "body": f"body {marker}",
            "type": "fact",
            "force": True,
        },
    ).json()
    personal_note = client.post(
        "/api/memories",
        headers=auth,
        json={
            "title": f"personal note {marker}",
            "body": f"body {marker}",
            "type": "note",
            "force": True,
        },
    ).json()
    group_pref = client.post(
        "/api/memories",
        headers=auth,
        json={
            "title": f"group pref {marker}",
            "body": f"body {marker}",
            "type": "preference",
            "space_id": group["id"],
            "force": True,
        },
    ).json()
    guide = client.post(
        "/api/guides",
        headers=auth,
        json={
            "title": f"guide {marker}",
            "body": f"When X do Y {marker}",
            "space_id": me["personal_space_id"],
        },
    ).json()

    assert personal_fact["created_by"] == me["id"]
    assert personal_fact["created_by_email"]
    owned = next(
        row
        for row in client.get("/api/memories", headers=auth).json()
        if row["id"] == personal_fact["id"]
    )
    assert owned["created_by"] == me["id"]
    assert owned["created_by_email"] == personal_fact["created_by_email"]

    personal = client.get("/api/memories?space_scope=personal", headers=auth).json()
    personal_ids = {row["id"] for row in personal}
    assert personal_fact["id"] in personal_ids
    assert personal_note["id"] in personal_ids
    assert guide["id"] in personal_ids
    assert group_pref["id"] not in personal_ids

    groups = client.get("/api/memories?space_scope=group", headers=auth).json()
    group_ids = {row["id"] for row in groups}
    assert group_pref["id"] in group_ids
    assert personal_fact["id"] not in group_ids

    facts_only = client.get("/api/memories?types=fact", headers=auth).json()
    fact_ids = {row["id"] for row in facts_only}
    assert personal_fact["id"] in fact_ids
    assert personal_note["id"] not in fact_ids
    assert guide["id"] not in fact_ids

    mixed = client.get("/api/memories?types=note,guide", headers=auth).json()
    mixed_ids = {row["id"] for row in mixed}
    assert personal_note["id"] in mixed_ids
    assert guide["id"] in mixed_ids
    assert personal_fact["id"] not in mixed_ids


def test_search_types_include_guides(client: TestClient) -> None:
    """Search respects multi-type tokens including guides."""
    token, _me = _user(client)
    auth = {"Authorization": f"Bearer {token}"}
    marker = uuid.uuid4().hex[:8]

    client.post(
        "/api/memories",
        headers=auth,
        json={
            "title": f"entry {marker}",
            "body": f"unique token {marker} for search",
            "type": "insight",
            "force": True,
        },
    )
    client.post(
        "/api/guides",
        headers=auth,
        json={
            "title": f"guide {marker}",
            "body": f"unique token {marker} for search guide path",
        },
    )

    both = client.get(
        f"/api/memories/search?q={marker}&types=insight,guide",
        headers=auth,
    ).json()
    kinds = {row["kind"] for row in both}
    assert "entry" in kinds
    assert "guide" in kinds
    assert all(row.get("space_id") and "space" not in row for row in both)

    guides = client.get(
        f"/api/memories/search?q={marker}&types=guide",
        headers=auth,
    ).json()
    assert guides
    assert all(row["kind"] == "guide" for row in guides)
    assert all(row.get("space_id") and "space" not in row for row in guides)


def _tool_result(client: TestClient, token: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call an MCP tool and return the raw result (``isError`` is set on a refused call)."""
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
    body = resp.json()
    return body.get("result") or {"isError": True, "error": body.get("error")}


def test_closed_set_parameters_are_validated(client: TestClient) -> None:
    """A bad kind or type is refused, never silently ignored or stored."""
    token, _me = _user(client)
    auth = {"Authorization": f"Bearer {token}"}
    marker = uuid.uuid4().hex[:8]
    entry = client.post(
        "/api/memories",
        headers=auth,
        json={"title": f"e {marker}", "body": f"entry {marker}", "type": "fact", "force": True},
    ).json()
    guide = client.post(
        "/api/guides", headers=auth, json={"title": f"g {marker}", "body": f"guide {marker}"}
    ).json()

    # browse: a misspelled kind is refused on both surfaces; the right one filters.
    assert _tool_result(client, token, "browse", {"kind": "guides"}).get("isError") is True
    typo = client.get("/api/memories", headers=auth, params={"kind": "guides"})
    assert typo.status_code in {400, 422}
    guides = client.get("/api/memories", headers=auth, params={"kind": "guide"}).json()
    assert {row["kind"] for row in guides} == {"guide"}

    # find is entries-only.
    refused_find = _tool_result(
        client, token, "find", {"query": f"g {marker} guide", "type": "guide"}
    )
    assert refused_find.get("isError") is True

    # revise: a guide has no entry type, and the refused call changes nothing.
    refused = _tool_result(
        client, token, "revise", {"id": guide["id"], "type": "fact", "title": "changed"}
    )
    assert refused.get("isError") is True
    patched = client.patch(f"/api/memories/{guide['id']}", headers=auth, json={"type": "fact"})
    assert patched.status_code in {400, 422}
    after = client.get(f"/api/memories/{guide['id']}", headers=auth).json()
    assert after["type"] is None
    assert after["title"] == f"g {marker}"

    # An entry can still change its type.
    ok = client.patch(f"/api/memories/{entry['id']}", headers=auth, json={"type": "decision"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["type"] == "decision"


def test_service_refuses_unknown_kind_and_guide_type() -> None:
    """The shared service refuses closed-set typos for REST and MCP alike."""
    from memchentic.services.errors import ValidationError
    from memchentic.services.memories import resolve_type_tokens

    with pytest.raises(ValidationError, match="kind must be"):
        resolve_type_tokens(kind="guides")
    with pytest.raises(ValidationError, match="type must be one of"):
        resolve_type_tokens(kind="entry", entry_type="guide")
    assert resolve_type_tokens(kind="") is None
    assert resolve_type_tokens(types=["note", "guide"]) == ["note", "guide"]


def test_type_and_kind_names_match_the_enums() -> None:
    """The Literal aliases used in tool schemas list exactly the enum values."""
    from memchentic.models.entities import EntryType, MemoryKind
    from memchentic.services.memories import EntryTypeName, MemoryKindName

    assert set(get_args(EntryTypeName)) == {e.value for e in EntryType}
    assert set(get_args(MemoryKindName)) == {k.value for k in MemoryKind}


def _auth(client: TestClient) -> dict[str, str]:
    return register_user(client).headers


def test_blank_search_returns_empty_without_error(client: TestClient) -> None:
    auth = _auth(client)
    resp = client.get("/api/memories/search", headers=auth, params={"q": "   "})
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_by_tag_mode_or_case_insensitive(client: TestClient) -> None:
    auth = _auth(client)
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


def test_by_tag_and_requires_all_or_requires_any(
    client: TestClient,
    make_user: Callable[..., RegisteredUser],
    mcp_tool: Callable[[dict[str, str], str, dict[str, Any]], dict[str, Any]],
) -> None:
    user = make_user()
    marker = uuid.uuid4().hex[:10]
    tag_a = f"a-{marker}"
    tag_b = f"b-{marker}"

    def create(tags: list[str]) -> str:
        resp = client.post(
            "/api/memories",
            headers=user.headers,
            json={
                "title": f"tagged {uuid.uuid4().hex[:8]}",
                "body": "x",
                "type": "fact",
                "tags": tags,
                "force": True,
            },
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["id"]

    both = create([tag_a, tag_b])
    only_a = create([tag_a])

    def ids(mode: str) -> set[str]:
        result = mcp_tool(user.headers, "by_tag", {"tags": [tag_a, tag_b], "mode": mode})
        return {row["id"] for row in tool_rows(result)}

    assert ids("and") == {both}
    assert ids("or") == {both, only_a}
