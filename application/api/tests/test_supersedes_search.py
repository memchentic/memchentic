"""Hybrid search hides targets of a live supersedes link."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient

from support import call_tool, register_user, tool_rows

EXPIRED = "2000-01-01T00:00:00+00:00"


def _user(client: TestClient) -> dict[str, str]:
    return register_user(client).headers


def _group(client: TestClient, auth: dict[str, str]) -> str:
    resp = client.post("/api/spaces", headers=auth, json={"name": f"ss-{uuid.uuid4().hex[:10]}"})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _entry(
    client: TestClient, auth: dict[str, str], space_id: str, **fields: Any
) -> dict[str, Any]:
    resp = client.post(
        "/api/memories",
        headers=auth,
        json={"type": "fact", "space_id": space_id, "force": True, **fields},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_search_hides_live_superseded_target(client: TestClient) -> None:
    auth = _user(client)
    space_id = _group(client, auth)
    marker = uuid.uuid4().hex[:8]
    old = _entry(client, auth, space_id, title=f"Old advice {marker}", body=f"legacy {marker}")
    new = _entry(
        client,
        auth,
        space_id,
        title=f"New advice {marker}",
        body=f"replacement {marker}",
        supersedes_id=old["id"],
    )

    search = client.get(
        "/api/memories/search",
        headers=auth,
        params={"q": marker, "space_id": space_id},
    )
    assert search.status_code == 200, search.text
    ids = {row["id"] for row in search.json()}
    assert new["id"] in ids
    assert old["id"] not in ids

    find = tool_rows(call_tool(client, auth, "find", {"query": marker, "space_id": space_id}))
    find_ids = {row["id"] for row in find}
    assert new["id"] in find_ids
    assert old["id"] not in find_ids

    opened = client.get(f"/api/memories/{old['id']}", headers=auth)
    assert opened.status_code == 200, opened.text
    assert opened.json()["id"] == old["id"]

    browse = client.get("/api/memories", headers=auth, params={"space_id": space_id})
    assert browse.status_code == 200, browse.text
    browse_ids = {row["id"] for row in browse.json()}
    assert old["id"] in browse_ids
    assert new["id"] in browse_ids


def test_search_returns_target_when_superseder_expires(client: TestClient) -> None:
    auth = _user(client)
    space_id = _group(client, auth)
    marker = uuid.uuid4().hex[:8]
    old = _entry(client, auth, space_id, title=f"Kept {marker}", body=f"body {marker}")
    new = _entry(
        client,
        auth,
        space_id,
        title=f"Temp {marker}",
        body=f"temp {marker}",
        supersedes_id=old["id"],
    )

    expired = client.patch(
        f"/api/memories/{new['id']}",
        headers=auth,
        json={"expires_at": EXPIRED},
    )
    assert expired.status_code == 200, expired.text

    search = client.get(
        "/api/memories/search",
        headers=auth,
        params={"q": marker, "space_id": space_id},
    )
    assert search.status_code == 200, search.text
    ids = {row["id"] for row in search.json()}
    assert old["id"] in ids
    assert new["id"] not in ids


def test_search_returns_target_when_superseder_forgotten(client: TestClient) -> None:
    auth = _user(client)
    space_id = _group(client, auth)
    marker = uuid.uuid4().hex[:8]
    old = _entry(client, auth, space_id, title=f"Base {marker}", body=f"base {marker}")
    new = _entry(
        client,
        auth,
        space_id,
        title=f"Replace {marker}",
        body=f"replace {marker}",
        supersedes_id=old["id"],
    )

    deleted = client.delete(f"/api/memories/{new['id']}", headers=auth)
    assert deleted.status_code == 200, deleted.text

    search = client.get(
        "/api/memories/search",
        headers=auth,
        params={"q": marker, "space_id": space_id},
    )
    assert search.status_code == 200, search.text
    ids = {row["id"] for row in search.json()}
    assert old["id"] in ids
    assert new["id"] not in ids
