"""Memory manage ACL: forget/relocate require creator, group manager, or admin."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import Principal
from memchentic.db.session import set_rls_context
from memchentic.models.entities import EntryType, Memory, MemoryKind
from memchentic.services import memories as memory_service
from memchentic.services.spaces import get_personal_space
from memchentic.services.users import register_internal_user


def _user(client: TestClient) -> tuple[str, str, str]:
    """Register and log in a fresh internal user.

    Returns:
        Tuple of (access token, user id, email).
    """
    email = f"t{uuid.uuid4().hex[:10]}@example.com"
    password = f"pw-{uuid.uuid4().hex}"
    client.post("/api/auth/register", json={"email": email, "password": password})
    token = client.post("/api/auth/login", json={"email": email, "password": password}).json()[
        "access_token"
    ]
    user_id = client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).json()["id"]
    return token, user_id, email


def _join(
    client: TestClient, owner_token: str, member_token: str, member_id: str, group_id: str
) -> None:
    """Owner invites ``member_id``; the member accepts."""
    owner = {"Authorization": f"Bearer {owner_token}"}
    member = {"Authorization": f"Bearer {member_token}"}
    added = client.post(
        f"/api/spaces/{group_id}/members", headers=owner, json={"user_id": member_id}
    )
    assert added.status_code == 200, added.text
    accepted = client.post(f"/api/spaces/{group_id}/accept", headers=member)
    assert accepted.status_code == 200, accepted.text


def _tool(client: TestClient, token: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call an MCP tool over streamable HTTP."""
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
    return resp.json()["result"]


def _shared_group(
    client: TestClient,
) -> tuple[str, str, str, str, str]:
    """Create owner A, member B, and a shared group owned by A.

    Returns:
        ``(token_a, token_b, group_id, user_a, user_b)``.
    """
    token_a, user_a, _ = _user(client)
    token_b, user_b, _ = _user(client)
    auth_a = {"Authorization": f"Bearer {token_a}"}
    group = client.post(
        "/api/spaces", headers=auth_a, json={"name": f"acl-{uuid.uuid4().hex[:8]}"}
    ).json()
    _join(client, token_a, token_b, user_b, group["id"])
    return token_a, token_b, group["id"], user_a, user_b


def test_member_cannot_forget_another_members_memory(client: TestClient) -> None:
    """Shared-group member cannot forget a memory they did not create."""
    token_a, token_b, group_id, _, _ = _shared_group(client)
    marker = uuid.uuid4().hex
    memory_id = _tool(
        client,
        token_a,
        "remember",
        {"title": f"owner-entry {marker}", "body": marker, "space_id": group_id},
    )["structuredContent"]["id"]

    refused = _tool(client, token_b, "forget", {"id": memory_id})
    assert refused.get("isError") is True

    still = _tool(client, token_a, "open", {"id": memory_id})
    assert still["structuredContent"]["id"] == memory_id


def test_creator_can_forget_own_memory_in_shared_group(client: TestClient) -> None:
    """Creator may forget their own shared-group memory."""
    _, token_b, group_id, _, _ = _shared_group(client)
    marker = uuid.uuid4().hex
    memory_id = _tool(
        client,
        token_b,
        "remember",
        {"title": f"member-entry {marker}", "body": marker, "space_id": group_id},
    )["structuredContent"]["id"]
    forgotten = _tool(client, token_b, "forget", {"id": memory_id})
    assert forgotten.get("isError") is not True
    missing = _tool(client, token_b, "open", {"id": memory_id})
    assert missing.get("isError") is True


def test_group_manager_can_forget_member_memory(client: TestClient) -> None:
    """Group creator (manager) may forget another member's memory."""
    token_a, token_b, group_id, _, _ = _shared_group(client)
    marker = uuid.uuid4().hex
    memory_id = _tool(
        client,
        token_b,
        "remember",
        {"title": f"member-entry {marker}", "body": marker, "space_id": group_id},
    )["structuredContent"]["id"]
    forgotten = _tool(client, token_a, "forget", {"id": memory_id})
    assert forgotten.get("isError") is not True
    missing = _tool(client, token_a, "open", {"id": memory_id})
    assert missing.get("isError") is True


def test_member_cannot_relocate_another_members_memory(client: TestClient) -> None:
    """Shared-group member cannot relocate a memory they did not create."""
    token_a, token_b, group_id, _, user_b = _shared_group(client)
    auth_a = {"Authorization": f"Bearer {token_a}"}
    marker = uuid.uuid4().hex
    memory_id = _tool(
        client,
        token_a,
        "remember",
        {"title": f"owner-entry {marker}", "body": marker, "space_id": group_id},
    )["structuredContent"]["id"]
    other = client.post(
        "/api/spaces", headers=auth_a, json={"name": f"other-{uuid.uuid4().hex[:8]}"}
    ).json()
    _join(client, token_a, token_b, user_b, other["id"])

    refused = _tool(client, token_b, "relocate", {"id": memory_id, "space_id": other["id"]})
    assert refused.get("isError") is True
    opened = _tool(client, token_a, "open", {"id": memory_id})
    assert opened["structuredContent"]["space_id"] == group_id


def test_creator_and_manager_can_relocate(client: TestClient) -> None:
    """Creator and group manager may relocate; peer member may not."""
    token_a, token_b, group_id, _, user_b = _shared_group(client)
    token_c, user_c, _ = _user(client)
    auth_a = {"Authorization": f"Bearer {token_a}"}
    _join(client, token_a, token_c, user_c, group_id)
    dest = client.post(
        "/api/spaces", headers=auth_a, json={"name": f"dest-{uuid.uuid4().hex[:8]}"}
    ).json()
    for tok, uid in ((token_b, user_b), (token_c, user_c)):
        _join(client, token_a, tok, uid, dest["id"])

    marker = uuid.uuid4().hex
    by_b = _tool(
        client,
        token_b,
        "remember",
        {"title": f"b-entry {marker}", "body": marker, "space_id": group_id, "force": True},
    )["structuredContent"]["id"]
    by_c = _tool(
        client,
        token_c,
        "remember",
        {"title": f"c-entry {marker}", "body": marker, "space_id": group_id, "force": True},
    )["structuredContent"]["id"]

    assert (
        _tool(client, token_c, "relocate", {"id": by_b, "space_id": dest["id"]}).get("isError")
        is True
    )
    moved = _tool(client, token_b, "relocate", {"id": by_b, "space_id": dest["id"]})
    assert moved.get("isError") is not True
    assert moved["structuredContent"]["space_id"] == dest["id"]
    moved_c = _tool(client, token_a, "relocate", {"id": by_c, "space_id": dest["id"]})
    assert moved_c.get("isError") is not True
    assert moved_c["structuredContent"]["space_id"] == dest["id"]


def test_member_cannot_revise_another_members_memory(client: TestClient) -> None:
    """revise needs the same right as forget: creator, group manager, or admin."""
    token_a, token_b, group_id, _, _ = _shared_group(client)
    auth_b = {"Authorization": f"Bearer {token_b}"}
    marker = uuid.uuid4().hex
    memory_id = _tool(
        client,
        token_a,
        "remember",
        {"title": f"owner-entry {marker}", "body": marker, "space_id": group_id},
    )["structuredContent"]["id"]

    refused = _tool(client, token_b, "revise", {"id": memory_id, "title": "x", "body": "x"})
    assert refused.get("isError") is True
    patched = client.patch(
        f"/api/memories/{memory_id}",
        headers=auth_b,
        json={"title": "x", "body": "x", "expires_at": "2000-01-01T00:00:00+00:00"},
    )
    assert patched.status_code == 403

    still = _tool(client, token_b, "open", {"id": memory_id})["structuredContent"]
    assert still["title"] == f"owner-entry {marker}"
    assert still["body"] == marker

    own = _tool(client, token_a, "revise", {"id": memory_id, "body": f"updated {marker}"})
    assert own.get("isError") is not True
    assert own["structuredContent"]["body"] == f"updated {marker}"


def test_group_manager_can_revise_member_memory(client: TestClient) -> None:
    """The group creator may revise a memory that a member created."""
    token_a, token_b, group_id, _, _ = _shared_group(client)
    marker = uuid.uuid4().hex
    memory_id = _tool(
        client,
        token_b,
        "remember",
        {"title": f"member-entry {marker}", "body": marker, "space_id": group_id},
    )["structuredContent"]["id"]
    revised = _tool(client, token_a, "revise", {"id": memory_id, "body": f"fixed {marker}"})
    assert revised.get("isError") is not True
    assert revised["structuredContent"]["body"] == f"fixed {marker}"


def test_guide_write_does_not_replace_another_members_guide(client: TestClient) -> None:
    """A title match on someone else's guide creates a separate guide."""
    token_a, token_b, group_id, user_a, user_b = _shared_group(client)
    marker = uuid.uuid4().hex[:8]
    title = f"Docker {marker}"
    body_a = f"Always pin base images by digest {marker}."
    body_b = f"Completely unrelated text {marker}."
    original = _tool(
        client, token_a, "guide_write", {"title": title, "body": body_a, "space_id": group_id}
    )["structuredContent"]

    args_b = {"title": title.upper(), "body": body_b, "space_id": group_id}
    other = _tool(client, token_b, "guide_write", args_b)["structuredContent"]
    assert other["id"] != original["id"]
    assert other["created_by"] == user_b

    # The member's own guide is the upsert target from now on.
    again = _tool(client, token_b, "guide_write", args_b)["structuredContent"]
    assert again["id"] == other["id"]
    assert again["confidence"] == 2.0

    kept = _tool(client, token_a, "open", {"id": original["id"]})["structuredContent"]
    assert kept["title"] == title
    assert kept["body"] == body_a
    assert kept["confidence"] == 1.0
    assert kept["created_by"] == user_a


def test_edits_record_the_editor(client: TestClient) -> None:
    """The last editor is recorded and returned next to the creator."""
    token_a, token_b, group_id, user_a, user_b = _shared_group(client)
    auth_a = {"Authorization": f"Bearer {token_a}"}
    email_a = client.get("/api/me", headers=auth_a).json()["email"]
    marker = uuid.uuid4().hex
    created = _tool(
        client,
        token_b,
        "remember",
        {"title": f"member-entry {marker}", "body": marker, "space_id": group_id},
    )["structuredContent"]
    assert created["created_by"] == user_b
    assert created["updated_by"] is None

    revised = _tool(client, token_a, "revise", {"id": created["id"], "body": f"edited {marker}"})[
        "structuredContent"
    ]
    assert revised["created_by"] == user_b
    assert revised["updated_by"] == user_a

    traced = _tool(client, token_b, "trace", {"id": created["id"]})["structuredContent"]
    assert traced["created_by"] == user_b
    assert traced["updated_by"] == user_a
    assert traced["updated_at"]

    found = _tool(client, token_b, "find", {"query": marker})["structuredContent"]["result"]
    digest = next(d for d in found if d["id"] == created["id"])
    assert digest["updated_by"] == user_a

    rest = client.get(f"/api/memories/{created['id']}", headers=auth_a).json()
    assert rest["updated_by"] == user_a
    assert rest["updated_by_email"] == email_a


async def test_open_memory_checks_membership_without_loading_roster(
    session: AsyncSession,
) -> None:
    """Open by id costs two statements and still hides the row from strangers."""
    user = await register_internal_user(
        session,
        email=f"roster-{uuid.uuid4().hex[:10]}@example.com",
        password="password-ok-12",
    )
    # register_internal_user binds the RLS context to the new user.
    await set_rls_context(session, user_id=None, is_admin=True)
    personal = await get_personal_space(session, user.id)
    assert personal is not None
    memory = Memory(
        space_id=personal.id,
        kind=MemoryKind.entry,
        entry_type=EntryType.note,
        title=f"roster {uuid.uuid4().hex[:8]}",
        body="x",
        created_by_user_id=user.id,
    )
    session.add(memory)
    await session.flush()
    memory_id = memory.id
    member = Principal(user_id=user.id)
    stranger = Principal(user_id=uuid.uuid4())
    statements: list[str] = []

    def before(*args: Any) -> None:
        statements.append(args[2])

    engine = session.get_bind().engine
    event.listen(engine, "before_cursor_execute", before)
    try:
        found = await memory_service.open_memory(session, member, memory_id=memory_id)
        found_id = found.id if found else None
        member_statements = len(statements)
        hidden = await memory_service.open_memory(session, stranger, memory_id=memory_id)
    finally:
        event.remove(engine, "before_cursor_execute", before)
        await session.rollback()
    assert found_id == memory_id
    assert hidden is None
    assert member_statements == 2
