"""REST memory routes: patch and delete."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from support import RegisteredUser, add_group_member, create_group


def _create(client: TestClient, headers: dict[str, str], **extra: Any) -> dict[str, Any]:
    marker = uuid.uuid4().hex[:12]
    payload = {
        "title": f"rest {marker}",
        "body": f"rest body {marker}",
        "type": "fact",
        "force": True,
        **extra,
    }
    resp = client.post("/api/memories", headers=headers, json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _listed(client: TestClient, headers: dict[str, str], memory_id: str) -> dict[str, Any]:
    """Read a row through browse, which does not count as a recall."""
    rows = client.get(
        "/api/memories", headers=headers, params={"include_expired": "true", "limit": 100}
    ).json()
    return next(row for row in rows if row["id"] == memory_id)


def test_patch_clear_expiry_and_type_change_persist(
    client: TestClient, make_user: Callable[..., RegisteredUser]
) -> None:
    user = make_user()
    created = _create(client, user.headers, ttl_days=5)
    assert created["expires_at"] is not None

    patched = client.patch(
        f"/api/memories/{created['id']}",
        headers=user.headers,
        json={"clear_expiry": True, "type": "decision"},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["expires_at"] is None
    assert patched.json()["type"] == "decision"

    stored = _listed(client, user.headers, created["id"])
    assert stored["expires_at"] is None
    assert stored["type"] == "decision"
    assert stored["title"] == created["title"]


def test_creator_delete_returns_deleted_then_404(
    client: TestClient, make_user: Callable[..., RegisteredUser]
) -> None:
    user = make_user()
    created = _create(client, user.headers)

    deleted = client.delete(f"/api/memories/{created['id']}", headers=user.headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"status": "deleted", "id": created["id"]}
    assert client.get(f"/api/memories/{created['id']}", headers=user.headers).status_code == 404


def test_member_cannot_delete_another_members_memory(
    client: TestClient, make_user: Callable[..., RegisteredUser]
) -> None:
    owner = make_user()
    member = make_user()
    group_id = create_group(client, owner.headers)
    add_group_member(client, owner.headers, group_id, member)
    created = _create(client, owner.headers, space_id=group_id)

    denied = client.delete(f"/api/memories/{created['id']}", headers=member.headers)
    assert denied.status_code == 403
    assert client.get(f"/api/memories/{created['id']}", headers=owner.headers).status_code == 200
