"""``overlap_report``: expired rows, scan truncation, and the conflicts note."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from memchentic.settings import get_settings
from support import call_tool, register_user

EXPIRED = "2000-01-01T00:00:00+00:00"


def _user(client: TestClient) -> dict[str, str]:
    return register_user(client).headers


def _tool(client: TestClient, auth: dict[str, str], name: str, args: dict[str, Any]) -> Any:
    return call_tool(client, auth, name, args)["structuredContent"]


def _group(client: TestClient, auth: dict[str, str]) -> str:
    resp = client.post("/api/spaces", headers=auth, json={"name": f"ovl-{uuid.uuid4().hex[:10]}"})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _entry(client: TestClient, auth: dict[str, str], space_id: str, **fields: Any) -> str:
    resp = client.post(
        "/api/memories",
        headers=auth,
        json={"type": "fact", "space_id": space_id, "force": True, **fields},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_overlap_report_hides_expired_rows_by_default(client: TestClient) -> None:
    auth = _user(client)
    space_id = _group(client, auth)
    title = f"Overlap {uuid.uuid4().hex[:8]}"
    expired = _entry(client, auth, space_id, title=title, body="one", expires_at=EXPIRED)
    live = _entry(client, auth, space_id, title=title, body="two")

    report = _tool(client, auth, "overlap_report", {"space_id": space_id})
    assert report["near_duplicates"] == []
    assert report["scanned"] == 1

    full = _tool(client, auth, "overlap_report", {"space_id": space_id, "include_expired": True})
    pairs = [p for p in full["near_duplicates"] if {p["a"], p["b"]} == {expired, live}]
    assert len(pairs) == 1
    assert pairs[0]["reason"] == "same_title"
    assert full["scanned"] == 2


def test_overlap_report_says_when_the_scan_is_truncated(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = _user(client)
    space_id = _group(client, auth)
    marker = uuid.uuid4().hex[:8]
    _entry(client, auth, space_id, title=f"First {marker}", body="alpha")
    _entry(client, auth, space_id, title=f"Second {marker}", body="beta")

    complete = _tool(client, auth, "overlap_report", {"space_id": space_id})
    assert complete["scanned"] == 2
    assert complete["truncated"] is False

    monkeypatch.setattr(get_settings(), "overlap_scan_limit", 1)
    partial = _tool(client, auth, "overlap_report", {"space_id": space_id})
    assert partial["scanned"] == 1
    assert partial["truncated"] is True


def test_overlap_report_lists_declared_supersedes_conflicts(client: TestClient) -> None:
    auth = _user(client)
    space_id = _group(client, auth)
    old_id = _entry(client, auth, space_id, title=f"Old {uuid.uuid4().hex[:8]}", body="old advice")
    new_resp = client.post(
        "/api/memories",
        headers=auth,
        json={
            "type": "fact",
            "space_id": space_id,
            "force": True,
            "title": f"New {uuid.uuid4().hex[:8]}",
            "body": "replacement advice",
            "supersedes_id": old_id,
        },
    )
    assert new_resp.status_code == 200, new_resp.text
    new_id = new_resp.json()["id"]
    assert new_resp.json()["supersedes_id"] == old_id

    report = _tool(client, auth, "overlap_report", {"space_id": space_id})
    assert "supersedes" in report["note"]
    pairs = [p for p in report["conflicts"] if {p["a"], p["b"]} == {new_id, old_id}]
    assert len(pairs) == 1
    assert pairs[0]["a"] == new_id
    assert pairs[0]["b"] == old_id
    assert pairs[0]["reason"] == "supersedes"

    expired = client.patch(
        f"/api/memories/{old_id}",
        headers=auth,
        json={"expires_at": EXPIRED},
    )
    assert expired.status_code == 200, expired.text
    hidden = _tool(client, auth, "overlap_report", {"space_id": space_id})
    assert all({p["a"], p["b"]} != {new_id, old_id} for p in hidden["conflicts"])

    with_expired = _tool(
        client, auth, "overlap_report", {"space_id": space_id, "include_expired": True}
    )
    assert any({p["a"], p["b"]} == {new_id, old_id} for p in with_expired["conflicts"])


def test_rest_overlap_matches_mcp_shape(client: TestClient) -> None:
    auth = _user(client)
    space_id = _group(client, auth)
    old_id = _entry(client, auth, space_id, title=f"RestOld {uuid.uuid4().hex[:8]}", body="a")
    new_id = _entry(
        client,
        auth,
        space_id,
        title=f"RestNew {uuid.uuid4().hex[:8]}",
        body="b",
        supersedes_id=old_id,
    )

    resp = client.get(
        "/api/memories/overlap",
        headers=auth,
        params={"space_id": space_id},
    )
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert {"near_duplicates", "expiring", "conflicts", "scanned", "truncated", "note"} <= set(
        report
    )
    assert report["content_trust"] == "untrusted"
    pairs = [p for p in report["conflicts"] if {p["a"], p["b"]} == {new_id, old_id}]
    assert len(pairs) == 1
    assert pairs[0]["reason"] == "supersedes"


def test_remember_rejects_cross_kind_supersedes(client: TestClient) -> None:
    auth = _user(client)
    space_id = _group(client, auth)
    guide = client.post(
        "/api/guides",
        headers=auth,
        json={
            "space_id": space_id,
            "title": f"Guide {uuid.uuid4().hex[:8]}",
            "body": "when X do Y",
        },
    )
    assert guide.status_code == 200, guide.text
    bad = client.post(
        "/api/memories",
        headers=auth,
        json={
            "type": "fact",
            "space_id": space_id,
            "force": True,
            "title": f"Entry {uuid.uuid4().hex[:8]}",
            "body": "cannot supersede a guide",
            "supersedes_id": guide.json()["id"],
        },
    )
    assert bad.status_code == 422, bad.text
