"""``overlap_report``: expired rows, scan truncation, and the conflicts note."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from memchentic.settings import get_settings

EXPIRED = "2000-01-01T00:00:00+00:00"


def _user(client: TestClient) -> dict[str, str]:
    email = f"ovl{uuid.uuid4().hex[:10]}@example.com"
    password = f"pw-{uuid.uuid4().hex}"
    client.post("/api/auth/register", json={"email": email, "password": password})
    token = client.post("/api/auth/login", json={"email": email, "password": password}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {token}"}


def _tool(client: TestClient, auth: dict[str, str], name: str, args: dict[str, Any]) -> Any:
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
    return resp.json()["result"]["structuredContent"]


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


def test_overlap_report_note_says_conflicts_are_not_implemented(client: TestClient) -> None:
    auth = _user(client)
    space_id = _group(client, auth)
    _entry(client, auth, space_id, title=f"Only {uuid.uuid4().hex[:8]}", body="gamma")

    report = _tool(client, auth, "overlap_report", {"space_id": space_id})

    assert report["conflicts"] == []
    assert "not implemented" in report["note"]
