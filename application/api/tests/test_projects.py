"""Projects: CRUD inside a space and optional memory.project_id."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi.testclient import TestClient

from support import RegisteredUser, create_group


def test_project_crud_and_memory_attach(
    client: TestClient, make_user: Callable[..., RegisteredUser]
) -> None:
    user = make_user()
    personal = client.get("/api/me", headers=user.headers).json()["personal_space_id"]
    created = client.post(
        "/api/projects",
        headers=user.headers,
        json={"space_id": personal, "name": f"proj-{uuid.uuid4().hex[:8]}"},
    )
    assert created.status_code == 200, created.text
    project = created.json()
    assert project["space_id"] == personal

    listed = client.get(f"/api/projects?space_id={personal}", headers=user.headers).json()
    assert project["id"] in {p["id"] for p in listed}

    memory = client.post(
        "/api/memories",
        headers=user.headers,
        json={
            "title": f"t-{uuid.uuid4().hex[:8]}",
            "body": "with project",
            "type": "note",
            "force": True,
            "project_id": project["id"],
        },
    )
    assert memory.status_code == 200, memory.text
    assert memory.json()["project_id"] == project["id"]
    assert memory.json().get("project_name") == project["name"]

    foreign = create_group(client, make_user().headers)
    other_proj = client.post(
        "/api/projects",
        headers=user.headers,
        json={"space_id": foreign, "name": "nope"},
    )
    # User is not a member of the foreign group.
    assert other_proj.status_code in (403, 404)

    group = create_group(client, user.headers)
    group_proj = client.post(
        "/api/projects",
        headers=user.headers,
        json={"space_id": group, "name": f"gproj-{uuid.uuid4().hex[:6]}"},
    ).json()
    bad = client.post(
        "/api/memories",
        headers=user.headers,
        json={
            "title": f"cross-{uuid.uuid4().hex[:8]}",
            "body": "wrong space project",
            "type": "note",
            "force": True,
            "space_id": personal,
            "project_id": group_proj["id"],
        },
    )
    assert bad.status_code == 404

    renamed = client.patch(
        f"/api/projects/{project['id']}",
        headers=user.headers,
        json={"name": f"renamed-{uuid.uuid4().hex[:6]}"},
    )
    assert renamed.status_code == 200, renamed.text

    deleted = client.delete(f"/api/projects/{project['id']}", headers=user.headers)
    assert deleted.status_code == 200, deleted.text
    cleared = client.get(f"/api/memories/{memory.json()['id']}", headers=user.headers).json()
    assert cleared["project_id"] is None
