"""Hygiene resolve endpoints/tools and Automation settings."""

from __future__ import annotations

import os
import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import Principal
from memchentic.db.session import set_rls_context
from memchentic.services import memories as memory_service
from memchentic.services.instance_settings import (
    get_automation_settings,
    set_automation_settings,
)
from memchentic.services.memory_hygiene import run_hygiene_automation
from memchentic.services.users import ensure_admin_user
from memchentic.settings import get_settings
from support import add_group_member, call_tool, create_group, register_user

ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "")


def _admin() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_KEY}"}


def _tool(client: TestClient, auth: dict[str, str], name: str, args: dict[str, Any]) -> Any:
    return call_tool(client, auth, name, args)["structuredContent"]


def _entry(client: TestClient, auth: dict[str, str], space_id: str, **fields: Any) -> str:
    resp = client.post(
        "/api/memories",
        headers=auth,
        json={"type": "fact", "space_id": space_id, "force": True, **fields},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_resolve_conflict_forgets_superseded(client: TestClient) -> None:
    user = register_user(client)
    auth = user.headers
    space_id = create_group(client, auth, prefix="hyg")
    old_id = _entry(client, auth, space_id, title=f"Old {uuid.uuid4().hex[:8]}", body="old")
    new_id = _entry(
        client,
        auth,
        space_id,
        title=f"New {uuid.uuid4().hex[:8]}",
        body="new",
        supersedes_id=old_id,
    )

    resp = client.post(
        "/api/memories/overlap/resolve-conflict",
        headers=auth,
        json={"a": new_id, "b": old_id},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "forgotten": old_id}
    assert client.get(f"/api/memories/{old_id}", headers=auth).status_code == 404
    assert client.get(f"/api/memories/{new_id}", headers=auth).status_code == 200


def test_resolve_conflict_rejects_wrong_pair(client: TestClient) -> None:
    user = register_user(client)
    auth = user.headers
    space_id = create_group(client, auth, prefix="hyg")
    a = _entry(client, auth, space_id, title=f"A {uuid.uuid4().hex[:8]}", body="a")
    b = _entry(client, auth, space_id, title=f"B {uuid.uuid4().hex[:8]}", body="b")
    resp = client.post(
        "/api/memories/overlap/resolve-conflict",
        headers=auth,
        json={"a": a, "b": b},
    )
    assert resp.status_code == 422, resp.text


def test_resolve_near_duplicate_keep_a(client: TestClient) -> None:
    user = register_user(client)
    auth = user.headers
    space_id = create_group(client, auth, prefix="hyg")
    title = f"Dup {uuid.uuid4().hex[:8]}"
    first = _entry(client, auth, space_id, title=title, body="one")
    second = _entry(client, auth, space_id, title=title, body="two")

    resp = client.post(
        "/api/memories/overlap/resolve-near-duplicate",
        headers=auth,
        json={"keep": first, "drop": second},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "forgotten": second}
    assert client.get(f"/api/memories/{first}", headers=auth).status_code == 200
    assert client.get(f"/api/memories/{second}", headers=auth).status_code == 404


def test_resolve_near_duplicate_rejects_unrelated(client: TestClient) -> None:
    user = register_user(client)
    auth = user.headers
    space_id = create_group(client, auth, prefix="hyg")
    a = _entry(client, auth, space_id, title=f"Alpha {uuid.uuid4().hex[:8]}", body="alpha body")
    b = _entry(client, auth, space_id, title=f"Beta {uuid.uuid4().hex[:8]}", body="beta body")
    resp = client.post(
        "/api/memories/overlap/resolve-near-duplicate",
        headers=auth,
        json={"keep": a, "drop": b},
    )
    assert resp.status_code == 422, resp.text


def test_resolve_conflict_forbidden_for_other_user(client: TestClient) -> None:
    owner = register_user(client)
    other = register_user(client)
    space_id = create_group(client, owner.headers, prefix="hyg")
    add_group_member(client, owner.headers, space_id, other)
    old_id = _entry(
        client, owner.headers, space_id, title=f"OwnOld {uuid.uuid4().hex[:8]}", body="old"
    )
    new_id = _entry(
        client,
        owner.headers,
        space_id,
        title=f"OwnNew {uuid.uuid4().hex[:8]}",
        body="new",
        supersedes_id=old_id,
    )
    denied = client.post(
        "/api/memories/overlap/resolve-conflict",
        headers=other.headers,
        json={"a": new_id, "b": old_id},
    )
    assert denied.status_code == 403, denied.text


def test_mcp_resolve_conflict_and_near_duplicate(client: TestClient) -> None:
    user = register_user(client)
    auth = user.headers
    space_id = create_group(client, auth, prefix="hyg")
    old_id = _entry(client, auth, space_id, title=f"McpOld {uuid.uuid4().hex[:8]}", body="old")
    new_id = _entry(
        client,
        auth,
        space_id,
        title=f"McpNew {uuid.uuid4().hex[:8]}",
        body="new",
        supersedes_id=old_id,
    )
    out = _tool(client, auth, "resolve_conflict", {"a": new_id, "b": old_id})
    assert out == {"ok": True, "forgotten": old_id}

    title = f"McpDup {uuid.uuid4().hex[:8]}"
    keep = _entry(client, auth, space_id, title=title, body="keep")
    drop = _entry(client, auth, space_id, title=title, body="drop")
    out2 = _tool(client, auth, "resolve_near_duplicate", {"keep": keep, "drop": drop})
    assert out2 == {"ok": True, "forgotten": drop}


def test_automation_settings_admin_only(client: TestClient) -> None:
    admin = _admin()
    user = register_user(client)

    denied = client.get("/api/system/automation", headers=user.headers)
    assert denied.status_code == 403, denied.text

    current = client.get("/api/system/automation", headers=admin)
    assert current.status_code == 200, current.text
    body = current.json()
    assert body["resolve_supersedes"] is False
    assert body["resolve_near_dups"] is False

    on = client.put(
        "/api/system/automation",
        headers=admin,
        json={"resolve_supersedes": True, "resolve_near_dups": False},
    )
    assert on.status_code == 200, on.text
    assert on.json()["resolve_supersedes"] is True
    assert on.json()["resolve_near_dups"] is False

    off = client.put(
        "/api/system/automation",
        headers=admin,
        json={"resolve_supersedes": False},
    )
    assert off.status_code == 200, off.text
    assert off.json()["resolve_supersedes"] is False


def test_automation_run_now_admin_only(client: TestClient) -> None:
    admin = _admin()
    user = register_user(client)
    auth = user.headers

    denied = client.post("/api/system/automation/run", headers=auth)
    assert denied.status_code == 403, denied.text

    idle = client.post("/api/system/automation/run", headers=admin)
    assert idle.status_code == 200, idle.text
    assert idle.json() == {
        "conflicts_forgotten": 0,
        "near_dups_forgotten": 0,
        "consolidations_applied": 0,
    }

    space_id = create_group(client, auth, prefix="autorun")
    old_id = _entry(client, auth, space_id, title=f"RunOld {uuid.uuid4().hex[:8]}", body="old")
    _entry(
        client,
        auth,
        space_id,
        title=f"RunNew {uuid.uuid4().hex[:8]}",
        body="new",
        supersedes_id=old_id,
    )
    on = client.put(
        "/api/system/automation",
        headers=admin,
        json={"resolve_supersedes": True},
    )
    assert on.status_code == 200, on.text
    ran = client.post("/api/system/automation/run", headers=admin)
    assert ran.status_code == 200, ran.text
    assert ran.json()["conflicts_forgotten"] >= 1
    assert client.get(f"/api/memories/{old_id}", headers=auth).status_code == 404
    client.put(
        "/api/system/automation",
        headers=admin,
        json={"resolve_supersedes": False, "resolve_near_dups": False},
    )


async def test_automation_pass_forgets_when_enabled(session: AsyncSession) -> None:
    settings = get_settings()
    admin_user = await ensure_admin_user(session, settings)
    principal = Principal(
        user_id=admin_user.id,
        is_admin=True,
        email=admin_user.email,
        display_name=admin_user.display_name,
    )
    await set_rls_context(session, user_id=principal.user_id, is_admin=True)
    await set_automation_settings(
        session, principal, resolve_supersedes=True, resolve_near_dups=True
    )
    await set_rls_context(session, user_id=principal.user_id, is_admin=True)

    marker = uuid.uuid4().hex[:8]
    old = await memory_service.remember(
        session,
        principal,
        title=f"AutoOld {marker}",
        body="old",
        entry_type="fact",
        force=True,
    )
    new = await memory_service.remember(
        session,
        principal,
        title=f"AutoNew {marker}",
        body="new",
        entry_type="fact",
        force=True,
        supersedes_id=old.id,
    )
    title = f"AutoDup {marker}"
    older = await memory_service.remember(
        session, principal, title=title, body="older first", entry_type="fact", force=True
    )
    newer = await memory_service.remember(
        session, principal, title=title, body="newer second", entry_type="fact", force=True
    )
    await memory_service.revise(session, principal, newer.id, body="newer touched")

    await set_rls_context(session, user_id=principal.user_id, is_admin=True)
    counts = await run_hygiene_automation(session)
    assert counts["conflicts_forgotten"] >= 1
    assert counts["near_dups_forgotten"] >= 1

    await set_rls_context(session, user_id=principal.user_id, is_admin=True)
    assert await memory_service.open_memory(session, principal, memory_id=old.id) is None
    assert await memory_service.open_memory(session, principal, memory_id=new.id) is not None
    assert await memory_service.open_memory(session, principal, memory_id=newer.id) is not None
    assert await memory_service.open_memory(session, principal, memory_id=older.id) is None

    await set_automation_settings(
        session, principal, resolve_supersedes=False, resolve_near_dups=False
    )


async def test_automation_pass_noop_when_off(session: AsyncSession) -> None:
    settings = get_settings()
    admin_user = await ensure_admin_user(session, settings)
    principal = Principal(
        user_id=admin_user.id,
        is_admin=True,
        email=admin_user.email,
        display_name=admin_user.display_name,
    )
    await set_rls_context(session, user_id=principal.user_id, is_admin=True)
    await set_automation_settings(
        session, principal, resolve_supersedes=False, resolve_near_dups=False
    )
    assert await get_automation_settings(session) == {
        "resolve_supersedes": False,
        "resolve_near_dups": False,
        "propose_consolidations": False,
        "auto_apply_consolidations": False,
    }
    await set_rls_context(session, user_id=principal.user_id, is_admin=True)
    marker = uuid.uuid4().hex[:8]
    old = await memory_service.remember(
        session,
        principal,
        title=f"NoopOld {marker}",
        body="old",
        entry_type="fact",
        force=True,
    )
    await memory_service.remember(
        session,
        principal,
        title=f"NoopNew {marker}",
        body="new",
        entry_type="fact",
        force=True,
        supersedes_id=old.id,
    )
    await set_rls_context(session, user_id=principal.user_id, is_admin=True)
    counts = await run_hygiene_automation(session)
    assert counts == {
        "conflicts_forgotten": 0,
        "near_dups_forgotten": 0,
        "consolidations_applied": 0,
    }
    await set_rls_context(session, user_id=principal.user_id, is_admin=True)
    assert await memory_service.open_memory(session, principal, memory_id=old.id) is not None
