"""Spaces: personal isolation, group lifecycle, write-target resolution, roster rules."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from support import RegisteredUser, add_group_member, call_tool, create_group, register_user


def _user(client: TestClient) -> tuple[str, str, str]:
    """Register and log in a fresh internal user.

    Args:
        client: In-process test client.

    Returns:
        Tuple of (access token, user id, email).
    """
    user = register_user(client)
    return user.token, user.user_id, user.email


def _tool(client: TestClient, token: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call an MCP tool over streamable HTTP.

    Args:
        client: In-process test client.
        token: Bearer token or API key.
        name: Tool name.
        args: Tool arguments.

    Returns:
        The JSON-RPC ``result`` object.
    """
    return call_tool(client, {"Authorization": f"Bearer {token}"}, name, args)


ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "")


def _auth(client: TestClient) -> dict[str, str]:
    return register_user(client).headers


def test_personal_isolation_and_group_lifecycle(client: TestClient) -> None:
    """Personal rows stay private; group rows are shared only while a member."""
    token_a, _, _ = _user(client)
    token_b, user_b, email_b = _user(client)
    marker = uuid.uuid4().hex

    entry = _tool(client, token_a, "remember", {"title": f"private {marker}", "body": marker})
    entry_id = entry["structuredContent"]["id"]
    found_b = _tool(client, token_b, "find", {"query": marker})["structuredContent"]["result"]
    assert entry_id not in {d["id"] for d in found_b}
    assert _tool(client, token_b, "open", {"id": entry_id}).get("isError") is True

    group = _tool(client, token_a, "space_create", {"name": f"group {marker}"})["structuredContent"]
    auth_a = {"Authorization": f"Bearer {token_a}"}
    auth_b = {"Authorization": f"Bearer {token_b}"}
    assert (
        client.post(
            f"/api/spaces/{group['id']}/members", headers=auth_a, json={"email": email_b}
        ).status_code
        == 200
    )
    assert client.post(f"/api/spaces/{group['id']}/accept", headers=auth_b).status_code == 200
    shared = _tool(
        client,
        token_a,
        "remember",
        {"title": f"shared {marker}", "body": marker, "space_id": group["id"]},
    )
    shared_id = shared["structuredContent"]["id"]
    assert _tool(client, token_b, "open", {"id": shared_id})["structuredContent"]["id"] == shared_id

    assert (
        client.post(
            f"/api/spaces/{group['id']}/members", headers=auth_b, json={"user_id": user_b}
        ).status_code
        == 403
    )
    assert (
        client.delete(f"/api/spaces/{group['id']}/members/{user_b}", headers=auth_b).status_code
        == 200
    )
    assert _tool(client, token_b, "open", {"id": shared_id}).get("isError") is True


def test_write_target_resolution(client: TestClient) -> None:
    """space_id beats group; group names and visibility=shared resolve without guessing."""
    token, _, _ = _user(client)
    auth = {"Authorization": f"Bearer {token}"}
    marker = uuid.uuid4().hex

    def write(**target: Any) -> Any:
        return client.post(
            "/api/memories",
            headers=auth,
            json={
                "title": f"t {uuid.uuid4().hex}",
                "body": f"{marker} {uuid.uuid4().hex}",
                "force": True,
                **target,
            },
        )

    me = client.get("/api/me", headers=auth).json()
    assert write().json()["space_id"] == me["personal_space_id"]

    ops = client.post("/api/spaces", headers=auth, json={"name": f"Ops {marker}"}).json()
    assert write(visibility="shared").json()["space_id"] == ops["id"]
    assert write(group=f"ops {marker}").json()["space_id"] == ops["id"]

    home = client.post("/api/spaces", headers=auth, json={"name": f"Home {marker}"}).json()
    assert write(space_id=home["id"], group=f"Ops {marker}").json()["space_id"] == home["id"]
    assert write(visibility="shared").status_code == 422
    assert write(group=f"missing {marker}").status_code == 422

    assert (
        client.patch(
            "/api/me", headers=auth, json={"default_shared_space_id": home["id"]}
        ).status_code
        == 200
    )
    assert write(visibility="shared").json()["space_id"] == home["id"]
    assert write(visibility="personal").json()["space_id"] == me["personal_space_id"]

    client.post("/api/spaces", headers=auth, json={"name": f"OPS {marker}"})
    assert write(group=f"Ops {marker}").status_code == 422

    outsider, _, _ = _user(client)
    other = client.post(
        "/api/spaces",
        headers={"Authorization": f"Bearer {outsider}"},
        json={"name": f"Other {marker}"},
    ).json()
    assert (
        client.patch(
            "/api/me", headers=auth, json={"default_shared_space_id": other["id"]}
        ).status_code
        == 404
    )


def test_roster_rules_and_no_email_enumeration(client: TestClient) -> None:
    """Only the creator manages the roster; outsiders cannot probe which emails exist."""
    token_a, user_a, _ = _user(client)
    token_b, _, email_b = _user(client)
    token_c, user_c, email_c = _user(client)
    auth = {t: {"Authorization": f"Bearer {t}"} for t in (token_a, token_b, token_c)}
    group = _tool(client, token_a, "space_create", {"name": f"roster {uuid.uuid4().hex}"})
    members = f"/api/spaces/{group['structuredContent']['id']}/members"

    outsider_known = client.post(members, headers=auth[token_c], json={"email": email_b})
    outsider_unknown = client.post(
        members, headers=auth[token_c], json={"email": f"nobody{uuid.uuid4().hex}@example.com"}
    )
    assert outsider_known.status_code == outsider_unknown.status_code == 404

    for email in (email_b, email_c):
        assert client.post(members, headers=auth[token_a], json={"email": email}).status_code == 200
    assert client.delete(f"{members}/{user_c}", headers=auth[token_b]).status_code == 404
    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}
    assert client.delete(f"{members}/{user_a}", headers=admin).status_code in (400, 403)


def test_admin_personal_space_id_is_own(client: TestClient) -> None:
    """Admin me.personal_space_id must be the admin user's personal space."""
    # Seed another personal space that is newer than the admin's.
    _user(client)
    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}
    me = client.get("/api/me", headers=admin).json()
    assert me["is_admin"] is True
    assert me.get("personal_space_id")
    assert me.get("id")
    write = client.post(
        "/api/memories",
        headers=admin,
        json={
            "title": f"admin-own-{uuid.uuid4().hex[:8]}",
            "body": "owned",
            "type": "fact",
            "space_id": me["personal_space_id"],
            "force": True,
        },
    )
    assert write.status_code == 200, write.text
    assert write.json()["created_by"] == me["id"]
    assert write.json()["space_id"] == me["personal_space_id"]


def test_shared_write_is_refused_for_a_group_the_caller_did_not_join(
    client: TestClient,
) -> None:
    """Being added to a group must not redirect visibility=shared writes."""
    token_a, _, _ = _user(client)
    token_b, user_b, _ = _user(client)
    auth_a = {"Authorization": f"Bearer {token_a}"}
    auth_b = {"Authorization": f"Bearer {token_b}"}
    group = client.post(
        "/api/spaces", headers=auth_a, json={"name": f"capture-{uuid.uuid4().hex[:8]}"}
    ).json()
    assert (
        client.post(
            f"/api/spaces/{group['id']}/members", headers=auth_a, json={"user_id": user_b}
        ).status_code
        == 200
    )
    write = client.post(
        "/api/memories",
        headers=auth_b,
        json={"title": f"t {uuid.uuid4().hex}", "body": "b", "visibility": "shared", "force": True},
    )
    assert write.status_code == 422, write.text


def test_added_member_must_accept_before_group_is_visible(client: TestClient) -> None:
    """An invite grants nothing until the invited user accepts it."""
    token_a, user_a, _ = _user(client)
    token_b, user_b, email_b = _user(client)
    auth_a = {"Authorization": f"Bearer {token_a}"}
    auth_b = {"Authorization": f"Bearer {token_b}"}
    marker = uuid.uuid4().hex
    group = client.post("/api/spaces", headers=auth_a, json={"name": f"invite {marker}"}).json()
    gid = group["id"]
    assert (
        client.post(
            f"/api/spaces/{gid}/members", headers=auth_a, json={"email": email_b}
        ).status_code
        == 200
    )
    guide = client.post(
        "/api/guides",
        headers=auth_a,
        json={"title": f"guide {marker}", "body": f"body {marker}", "space_id": gid},
    ).json()

    def b_guide_ids() -> set[str]:
        found = _tool(client, token_b, "guide_find", {"query": f"guide {marker}"})
        return {d["id"] for d in found["structuredContent"]["result"]}

    def b_write(**target: Any) -> int:
        return client.post(
            "/api/memories",
            headers=auth_b,
            json={"title": f"t {uuid.uuid4().hex}", "body": marker, "force": True, **target},
        ).status_code

    # Before accept: nothing from the group reaches B, and B cannot write into it.
    me_b = client.get("/api/me", headers=auth_b).json()
    assert gid not in {m["id"] for m in me_b["memberships"]}
    assert [i["id"] for i in me_b["pending_invites"]] == [gid]
    assert guide["id"] not in b_guide_ids()
    assert _tool(client, token_b, "open", {"id": guide["id"]}).get("isError") is True
    assert client.get(f"/api/spaces/{gid}", headers=auth_b).status_code == 404
    assert b_write(visibility="shared") == 422
    assert b_write(space_id=gid) == 404
    mcp_me = _tool(client, token_b, "me", {})["structuredContent"]
    assert mcp_me["pending_invite_count"] == 1
    assert "pending_invites" not in mcp_me
    roster = client.get(f"/api/spaces/{gid}", headers=auth_a).json()["members"]
    assert {m["user_id"] for m in roster} == {user_a}
    assert user_b not in {u["id"] for u in client.get("/api/users", headers=auth_a).json()}

    # Accept (idempotent): the group opens up.
    for _ in range(2):
        accepted = client.post(f"/api/spaces/{gid}/accept", headers=auth_b)
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["id"] == gid
    me_b = client.get("/api/me", headers=auth_b).json()
    assert gid in {m["id"] for m in me_b["memberships"]}
    assert me_b["pending_invites"] == []
    assert guide["id"] in b_guide_ids()
    shared = client.post(
        "/api/memories",
        headers=auth_b,
        json={"title": f"s {marker}", "body": marker, "visibility": "shared", "force": True},
    )
    assert shared.status_code == 200, shared.text
    assert shared.json()["space_id"] == gid
    roster = client.get(f"/api/spaces/{gid}", headers=auth_a).json()["members"]
    assert {m["user_id"] for m in roster} == {user_a, user_b}
    assert all(m["pending"] is False for m in roster)


def test_invite_can_be_declined_and_cannot_be_accepted_by_others(client: TestClient) -> None:
    """Only the invited user can accept; declining removes the invite."""
    token_a, _, _ = _user(client)
    token_b, user_b, _ = _user(client)
    token_c, _, _ = _user(client)
    auth_a = {"Authorization": f"Bearer {token_a}"}
    auth_b = {"Authorization": f"Bearer {token_b}"}
    auth_c = {"Authorization": f"Bearer {token_c}"}
    group = client.post(
        "/api/spaces", headers=auth_a, json={"name": f"decline-{uuid.uuid4().hex[:8]}"}
    ).json()
    gid = group["id"]
    assert (
        client.post(
            f"/api/spaces/{gid}/members", headers=auth_a, json={"user_id": user_b}
        ).status_code
        == 200
    )

    assert client.post(f"/api/spaces/{gid}/accept", headers=auth_c).status_code == 404
    # C cannot see the group: SEC-051 maps "Group not found" to 404.
    assert client.delete(f"/api/spaces/{gid}/members/{user_b}", headers=auth_c).status_code == 404

    assert client.delete(f"/api/spaces/{gid}/members/{user_b}", headers=auth_b).status_code == 200
    assert client.get("/api/me", headers=auth_b).json()["pending_invites"] == []
    assert client.post(f"/api/spaces/{gid}/accept", headers=auth_b).status_code == 404


def test_admin_memberships_list_only_own_spaces(client: TestClient) -> None:
    """Admin `memberships` are the admin's own spaces, not every space."""
    other = _auth(client)
    other_personal = client.get("/api/me", headers=other).json()["personal_space_id"]
    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}

    me = client.get("/api/me", headers=admin).json()
    personals = [m["id"] for m in me["memberships"] if m["kind"] == "personal"]
    assert personals == [me["personal_space_id"]]
    assert other_personal not in {m["id"] for m in me["memberships"]}

    mcp = client.post(
        "/mcp",
        headers={**admin, "Accept": "application/json, text/event-stream"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "me", "arguments": {}},
        },
    )
    mcp.raise_for_status()
    mcp_me = mcp.json()["result"]["structuredContent"]
    assert other_personal not in {m["id"] for m in mcp_me["memberships"]}
    assert mcp_me["is_admin"] is True

    # The admin directory is unchanged: every space is still listed there.
    directory = {s["id"] for s in client.get("/api/spaces", headers=admin).json()}
    assert other_personal in directory


def test_rename_requires_group_manager(
    client: TestClient, make_user: Callable[..., RegisteredUser]
) -> None:
    owner = make_user()
    member = make_user()
    outsider = make_user()
    group_id = create_group(client, owner.headers)
    add_group_member(client, owner.headers, group_id, member)
    url = f"/api/spaces/{group_id}"

    assert client.patch(url, headers=member.headers, json={"name": "by-member"}).status_code == 403
    assert (
        client.patch(url, headers=outsider.headers, json={"name": "by-outsider"}).status_code == 404
    )

    new_name = f"renamed-{uuid.uuid4().hex[:8]}"
    renamed = client.patch(url, headers=owner.headers, json={"name": new_name})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == new_name
    assert client.get(url, headers=owner.headers).json()["name"] == new_name


def test_non_member_cannot_view_group(
    client: TestClient, make_user: Callable[..., RegisteredUser]
) -> None:
    owner = make_user()
    outsider = make_user()
    group_id = create_group(client, owner.headers)

    assert client.get(f"/api/spaces/{group_id}", headers=outsider.headers).status_code == 404
    listed = client.get("/api/spaces", headers=outsider.headers).json()
    assert group_id not in {space["id"] for space in listed}


def test_delete_group_removes_memories_and_memberships(
    client: TestClient, make_user: Callable[..., RegisteredUser]
) -> None:
    owner = make_user()
    member = make_user()
    group_id = create_group(client, owner.headers)
    add_group_member(client, owner.headers, group_id, member)
    written = client.post(
        "/api/memories",
        headers=member.headers,
        json={
            "title": f"in group {uuid.uuid4().hex[:8]}",
            "body": "x",
            "space_id": group_id,
            "force": True,
        },
    )
    assert written.status_code == 200, written.text
    memory_id = written.json()["id"]

    personal_id = client.get("/api/me", headers=owner.headers).json()["personal_space_id"]
    assert client.delete(f"/api/spaces/{personal_id}", headers=owner.headers).status_code == 404
    assert client.delete(f"/api/spaces/{group_id}", headers=member.headers).status_code == 403

    deleted = client.delete(f"/api/spaces/{group_id}", headers=owner.headers)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"status": "ok"}

    assert client.get(f"/api/spaces/{group_id}", headers=owner.headers).status_code == 404
    assert client.get(f"/api/memories/{memory_id}", headers=member.headers).status_code == 404
    memberships = client.get("/api/me", headers=member.headers).json()["memberships"]
    assert group_id not in {space["id"] for space in memberships}


def _shared_write(client: TestClient, headers: dict[str, str]) -> Any:
    return client.post(
        "/api/memories",
        headers=headers,
        json={
            "title": f"shared {uuid.uuid4().hex[:8]}",
            "body": "x",
            "visibility": "shared",
            "force": True,
        },
    )


def test_clear_default_shared_space(
    client: TestClient, make_user: Callable[..., RegisteredUser]
) -> None:
    user = make_user()
    first = create_group(client, user.headers)
    create_group(client, user.headers)

    chosen = client.patch("/api/me", headers=user.headers, json={"default_shared_space_id": first})
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["default_shared_space_id"] == first
    landed = _shared_write(client, user.headers)
    assert landed.status_code == 200, landed.text
    assert landed.json()["space_id"] == first

    cleared = client.patch("/api/me", headers=user.headers, json={"default_shared_space_id": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["default_shared_space_id"] is None
    assert _shared_write(client, user.headers).status_code == 422


def test_default_shared_space_must_be_own_group(
    client: TestClient, make_user: Callable[..., RegisteredUser]
) -> None:
    user = make_user()
    other = make_user()
    foreign = create_group(client, other.headers)

    denied = client.patch(
        "/api/me", headers=user.headers, json={"default_shared_space_id": foreign}
    )
    assert denied.status_code == 404
    assert client.get("/api/me", headers=user.headers).json()["default_shared_space_id"] is None
