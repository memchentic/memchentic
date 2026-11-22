"""Projects: CRUD inside a space and optional memory.project_id."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable

from fastapi.testclient import TestClient

from support import RegisteredUser, add_group_member, call_tool, create_group, tool_rows

ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "")


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

    # Unfiltered list includes every readable project.
    all_listed = client.get("/api/projects", headers=user.headers).json()
    assert project["id"] in {p["id"] for p in all_listed}

    shown = client.get(f"/api/projects/{project['id']}", headers=user.headers)
    assert shown.status_code == 200, shown.text
    assert shown.json()["name"] == project["name"]

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

    assert client.get(f"/api/projects?space_id={foreign}", headers=user.headers).status_code == 404
    assert client.get(f"/api/projects/{uuid.uuid4()}", headers=user.headers).status_code == 404

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

    clash = client.post(
        "/api/projects",
        headers=user.headers,
        json={"space_id": personal, "name": project["name"]},
    )
    assert clash.status_code == 409

    renamed = client.patch(
        f"/api/projects/{project['id']}",
        headers=user.headers,
        json={"name": f"renamed-{uuid.uuid4().hex[:6]}"},
    )
    assert renamed.status_code == 200, renamed.text

    other = client.post(
        "/api/projects",
        headers=user.headers,
        json={"space_id": personal, "name": f"other-{uuid.uuid4().hex[:6]}"},
    ).json()
    rename_clash = client.patch(
        f"/api/projects/{other['id']}",
        headers=user.headers,
        json={"name": renamed.json()["name"]},
    )
    assert rename_clash.status_code == 409

    deleted = client.delete(f"/api/projects/{project['id']}", headers=user.headers)
    assert deleted.status_code == 200, deleted.text
    cleared = client.get(f"/api/memories/{memory.json()['id']}", headers=user.headers).json()
    assert cleared["project_id"] is None

    assert (
        client.patch(
            f"/api/projects/{uuid.uuid4()}",
            headers=user.headers,
            json={"name": "ghost"},
        ).status_code
        == 404
    )
    assert client.delete(f"/api/projects/{uuid.uuid4()}", headers=user.headers).status_code == 404


def test_group_admin_and_member_project_permissions(
    client: TestClient, make_user: Callable[..., RegisteredUser]
) -> None:
    owner = make_user()
    admin_member = make_user()
    plain = make_user()
    gid = create_group(client, owner.headers)
    add_group_member(client, owner.headers, gid, admin_member)
    add_group_member(client, owner.headers, gid, plain)
    assert (
        client.patch(
            f"/api/spaces/{gid}/members/{admin_member.user_id}",
            headers=owner.headers,
            json={"role": "admin"},
        ).status_code
        == 200
    )

    created = client.post(
        "/api/projects",
        headers=owner.headers,
        json={"space_id": gid, "name": f"owned-{uuid.uuid4().hex[:6]}"},
    )
    assert created.status_code == 200, created.text
    project_id = created.json()["id"]

    # Plain member cannot rename or delete the owner's project.
    assert (
        client.patch(
            f"/api/projects/{project_id}",
            headers=plain.headers,
            json={"name": "hacked"},
        ).status_code
        == 403
    )
    assert client.delete(f"/api/projects/{project_id}", headers=plain.headers).status_code == 403

    # Group admin can rename; instance admin can delete.
    renamed = client.patch(
        f"/api/projects/{project_id}",
        headers=admin_member.headers,
        json={"name": f"by-admin-{uuid.uuid4().hex[:6]}"},
    )
    assert renamed.status_code == 200, renamed.text

    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}
    assert client.delete(f"/api/projects/{project_id}", headers=admin).status_code == 200


def test_mcp_project_tools(client: TestClient, make_user: Callable[..., RegisteredUser]) -> None:
    user = make_user()
    personal = client.get("/api/me", headers=user.headers).json()["personal_space_id"]
    name = f"mcp-{uuid.uuid4().hex[:8]}"
    created = call_tool(
        client, user.headers, "project_create", {"space_id": personal, "name": name}
    )
    assert created.get("isError") is not True, created
    project = created["structuredContent"]
    assert project["name"] == name

    listed = tool_rows(call_tool(client, user.headers, "project_list", {"space_id": personal}))
    assert project["id"] in {p["id"] for p in listed}

    shown = call_tool(client, user.headers, "project_show", {"id": project["id"]})
    assert shown.get("isError") is not True, shown
    assert shown["structuredContent"]["id"] == project["id"]

    new_name = f"mcp-r-{uuid.uuid4().hex[:6]}"
    renamed = call_tool(
        client, user.headers, "project_rename", {"id": project["id"], "name": new_name}
    )
    assert renamed.get("isError") is not True, renamed
    assert renamed["structuredContent"]["name"] == new_name

    deleted = call_tool(client, user.headers, "project_delete", {"id": project["id"]})
    assert deleted.get("isError") is not True, deleted
