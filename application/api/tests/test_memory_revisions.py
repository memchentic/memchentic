"""Revision history on revise."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from support import register_user


def _auth(client: TestClient) -> dict[str, str]:
    return register_user(client).headers


def test_revise_creates_history(client: TestClient) -> None:
    auth = _auth(client)
    marker = uuid.uuid4().hex[:12]
    created = client.post(
        "/api/memories",
        headers=auth,
        json={
            "title": f"rev {marker}",
            "body": f"before {marker}",
            "type": "fact",
            "force": True,
        },
    )
    assert created.status_code == 200, created.text
    memory_id = created.json()["id"]

    patched = client.patch(
        f"/api/memories/{memory_id}",
        headers=auth,
        json={"title": f"rev {marker} v2", "body": f"after {marker}"},
    )
    assert patched.status_code == 200, patched.text

    history = client.get(f"/api/memories/{memory_id}/history", headers=auth)
    assert history.status_code == 200, history.text
    rows = history.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "revise"
    assert set(row["changed_fields"]) >= {"title", "body"}
    assert row["snapshot"]["body"] == f"before {marker}"
    assert row["snapshot"]["title"] == f"rev {marker}"


def test_export_respects_acl(client: TestClient) -> None:
    user_a = register_user(client)
    user_b = register_user(client)
    marker = uuid.uuid4().hex[:12]
    created = client.post(
        "/api/memories",
        headers=user_a.headers,
        json={
            "title": f"private {marker}",
            "body": f"secret {marker}",
            "type": "fact",
            "visibility": "personal",
            "force": True,
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()["body"]

    denied = client.get("/api/memories/export", headers=user_b.headers)
    assert denied.status_code == 200
    assert body not in denied.text
