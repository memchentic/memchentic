"""Coverage for export, event log, webhooks, ingest LLM, and consolidations."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import Principal
from memchentic.db.session import set_rls_context
from memchentic.events.memory_bus import MemoryEvent
from memchentic.services import instance_settings as instance_settings_service
from memchentic.services import memories as memory_service
from memchentic.services import memory_ingest
from memchentic.services.instance_settings import (
    increment_webhook_fail_count,
    set_automation_settings,
    set_webhook_settings,
)
from memchentic.services.memory_events_log import append_memory_event, list_memory_event_log
from memchentic.services.memory_export import export_memories
from memchentic.services.memory_feedback import feedback_scores_for_ids, submit_feedback
from memchentic.services.memory_hygiene import _consolidation_proposals, run_hygiene_automation
from memchentic.services.memory_revisions import list_history
from memchentic.services.memory_webhooks import (
    _deliver_once,
    _run_delivery,
    _sign_body,
    schedule_webhook_delivery,
)
from memchentic.services.users import ensure_admin_user
from memchentic.settings import get_settings
from support import call_tool, create_group, register_user

ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "")


def _admin() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_KEY}"}


def test_export_jsonl_entry_and_guide(client: TestClient) -> None:
    auth = register_user(client).headers
    entry = client.post(
        "/api/memories",
        headers=auth,
        json={
            "title": f"Export entry {uuid.uuid4().hex[:8]}",
            "body": "support preference: prefer email",
            "type": "preference",
            "force": True,
        },
    )
    assert entry.status_code == 200, entry.text
    guide = client.post(
        "/api/guides",
        headers=auth,
        json={
            "title": f"Export guide {uuid.uuid4().hex[:8]}",
            "body": "When billing fails, escalate to finance.",
            "force": True,
        },
    )
    assert guide.status_code == 200, guide.text

    peer = register_user(client, prefix="peer").headers
    denied = client.get("/api/memories/export", headers=peer)
    assert denied.status_code == 200
    peer_ids = {json.loads(line)["id"] for line in denied.text.splitlines() if line.strip()}
    assert entry.json()["id"] not in peer_ids
    assert guide.json()["id"] not in peer_ids

    resp = client.get("/api/memories/export", headers=auth)
    assert resp.status_code == 200, resp.text
    assert "application/x-ndjson" in resp.headers.get("content-type", "")
    rows = [json.loads(line) for line in resp.text.splitlines() if line.strip()]
    by_id = {row["id"]: row for row in rows}
    assert entry.json()["id"] in by_id
    assert guide.json()["id"] in by_id
    assert by_id[entry.json()["id"]]["kind"] == "entry"
    assert by_id[guide.json()["id"]]["kind"] == "guide"
    assert by_id[entry.json()["id"]]["body"]


def test_memory_event_log_lists_create(client: TestClient) -> None:
    auth = register_user(client).headers
    created = client.post(
        "/api/memories",
        headers=auth,
        json={
            "title": f"Event log {uuid.uuid4().hex[:8]}",
            "body": "ops note for durable log",
            "type": "note",
            "force": True,
        },
    )
    assert created.status_code == 200, created.text
    mid = created.json()["id"]
    log = client.get("/api/events/memory-log", headers=auth)
    assert log.status_code == 200, log.text
    rows = log.json()
    assert isinstance(rows, list)
    assert any(row.get("memory_id") == mid and row.get("action") == "created" for row in rows)

    peer = register_user(client, prefix="elog").headers
    peer_log = client.get("/api/events/memory-log", headers=peer)
    assert peer_log.status_code == 200
    assert mid not in {row.get("memory_id") for row in peer_log.json()}


def test_webhook_settings_admin_only(client: TestClient) -> None:
    user = register_user(client).headers
    assert client.get("/api/system/webhooks", headers=user).status_code == 403
    got = client.get("/api/system/webhooks", headers=_admin())
    assert got.status_code == 200, got.text
    body = got.json()
    assert "enabled" in body
    assert "secret" not in body
    put = client.put(
        "/api/system/webhooks",
        headers=_admin(),
        json={
            "enabled": True,
            "url": "https://hooks.example.test/mem",
            "secret": "webhook-secret-value",
        },
    )
    assert put.status_code == 200, put.text
    assert put.json()["enabled"] is True
    assert put.json()["url"] == "https://hooks.example.test/mem"
    assert put.json()["secret_set"] is True


@pytest.mark.asyncio
async def test_webhook_deliver_once_signs_and_posts() -> None:
    payload = {"action": "created", "id": str(uuid.uuid4())}
    secret = "s3cret"

    class _Resp:
        def raise_for_status(self) -> None:
            return None

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_Resp())
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("memchentic.services.memory_webhooks.httpx.AsyncClient", return_value=mock_client):
        ok = await _deliver_once(payload, "https://hooks.example.test/x", secret)
    assert ok is True
    args, kwargs = mock_client.post.call_args
    assert args[0] == "https://hooks.example.test/x"
    body = kwargs["content"]
    assert kwargs["headers"]["X-Memchentic-Signature"] == _sign_body(secret, body)


@pytest.mark.asyncio
async def test_webhook_deliver_once_failure() -> None:
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.ConnectError("down"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    with patch("memchentic.services.memory_webhooks.httpx.AsyncClient", return_value=mock_client):
        ok = await _deliver_once({"a": 1}, "https://hooks.example.test/x", "")
    assert ok is False


def test_schedule_webhook_noop_under_pytest() -> None:
    event = MemoryEvent(
        action="created",
        id=str(uuid.uuid4()),
        space_id=str(uuid.uuid4()),
        kind="entry",
        type="note",
        title="t",
        tags=[],
        snippet="s",
    )
    schedule_webhook_delivery(event)


def test_schedule_webhook_creates_task(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    event = MemoryEvent(
        action="created",
        id=str(uuid.uuid4()),
        space_id=str(uuid.uuid4()),
        kind="entry",
        type="note",
        title="t",
        tags=[],
        snippet="s",
    )
    created: list[Any] = []

    class _Loop:
        def create_task(self, coro: Any, name: str | None = None) -> Any:
            created.append(name)
            task = MagicMock()
            task.cancelled.return_value = False
            task.exception.return_value = None
            task.add_done_callback = MagicMock()
            coro.close()
            return task

        def is_closed(self) -> bool:
            return False

    loop_patch = "memchentic.services.memory_webhooks.asyncio.get_running_loop"
    with patch(loop_patch, return_value=_Loop()):
        schedule_webhook_delivery(event)
    assert created == ["memchentic-webhook"]


@pytest.mark.asyncio
async def test_ingest_llm_success_and_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_LLM_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("MEMORY_LLM_API_KEY", "k")
    monkeypatch.setenv("MEMORY_LLM_MODEL", "test-model")
    from memchentic.settings import get_settings

    get_settings.cache_clear()

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                [
                                    {
                                        "kind": "entry",
                                        "title": "LLM pref",
                                        "body": "Prefer SMS",
                                        "entry_type": "preference",
                                    }
                                ]
                            )
                        }
                    }
                ]
            }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_Resp())
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    with patch("memchentic.services.memory_ingest.httpx.AsyncClient", return_value=mock_client):
        rows = await memory_ingest.suggest_candidates("x", use_llm=True)
    assert rows[0]["title"] == "LLM pref"

    mock_client.post = AsyncMock(side_effect=httpx.HTTPError("boom"))
    with patch("memchentic.services.memory_ingest.httpx.AsyncClient", return_value=mock_client):
        fallback = await memory_ingest.suggest_candidates(
            "## Policy\n\nAlways confirm identity.\n",
            use_llm=True,
        )
    assert fallback
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_ingest_llm_skipped_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_LLM_BASE_URL", "")
    from memchentic.settings import get_settings

    get_settings.cache_clear()
    assert await memory_ingest.llm_suggest_candidates("hello") is None
    get_settings.cache_clear()


def test_consolidation_proposals_keep_newer() -> None:
    from datetime import UTC, datetime

    left = MagicMock()
    left.id = uuid.uuid4()
    left.updated_at = datetime(2024, 1, 1, tzinfo=UTC)
    right = MagicMock()
    right.id = uuid.uuid4()
    right.updated_at = datetime(2024, 2, 1, tzinfo=UTC)
    proposals = _consolidation_proposals(
        [{"a": str(left.id), "b": str(right.id), "title": "Dup"}],
        {str(left.id): left, str(right.id): right},
    )
    assert len(proposals) == 1
    assert proposals[0]["keep"] == str(right.id)
    assert proposals[0]["drop"] == str(left.id)
    assert proposals[0]["action"] == "merge"


def test_overlap_report_includes_proposals(client: TestClient) -> None:
    auth = register_user(client).headers
    space_id = create_group(client, auth, prefix="prop")
    title = f"Same title {uuid.uuid4().hex[:8]}"
    for body in ("first draft", "second draft"):
        resp = client.post(
            "/api/memories",
            headers=auth,
            json={
                "title": title,
                "body": body,
                "type": "note",
                "space_id": space_id,
                "force": True,
            },
        )
        assert resp.status_code == 200, resp.text
    report = client.get("/api/memories/overlap", headers=auth, params={"space_id": space_id})
    assert report.status_code == 200, report.text
    payload = report.json()
    assert "proposals" in payload
    assert isinstance(payload["proposals"], list)


def test_mcp_export_and_history_tools(client: TestClient) -> None:
    auth = register_user(client).headers
    created = client.post(
        "/api/memories",
        headers=auth,
        json={
            "title": f"MCP export {uuid.uuid4().hex[:8]}",
            "body": "research digest line",
            "type": "insight",
            "force": True,
        },
    )
    assert created.status_code == 200, created.text
    mid = created.json()["id"]
    client.patch(
        f"/api/memories/{mid}",
        headers=auth,
        json={"body": "research digest line revised"},
    )
    hist = call_tool(client, auth, "history", {"id": mid})
    assert hist.get("isError") is not True
    rows = hist.get("structuredContent") or hist
    assert isinstance(rows, (list, dict))
    exported = call_tool(client, auth, "export", {"limit": 5})
    assert exported.get("isError") is not True


async def _admin_principal(session: AsyncSession) -> Principal:
    settings = get_settings()
    admin_user = await ensure_admin_user(session, settings)
    return Principal(
        user_id=admin_user.id,
        is_admin=True,
        email=admin_user.email,
        display_name=admin_user.display_name,
    )


class _WebhookSessionFactory:
    async def __aenter__(self) -> AsyncSession:
        return MagicMock()

    async def __aexit__(self, *args: object) -> None:
        return None

    def __call__(self) -> _WebhookSessionFactory:
        return self


def _patch_run_delivery_db(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    admin = MagicMock(id=uuid.uuid4())
    monkeypatch.setattr(
        "memchentic.services.memory_webhooks.ensure_admin_user",
        AsyncMock(return_value=admin),
    )
    monkeypatch.setattr(
        "memchentic.services.memory_webhooks.set_rls_context",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "memchentic.services.memory_webhooks.get_session_factory",
        lambda: _WebhookSessionFactory(),
    )
    deliver = AsyncMock(return_value=True)
    monkeypatch.setattr("memchentic.services.memory_webhooks._deliver_once", deliver)
    return deliver


@pytest.mark.asyncio
async def test_run_delivery_disabled_webhook_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    deliver = _patch_run_delivery_db(monkeypatch)
    disabled = instance_settings_service.WebhookSettings(
        enabled=False,
        url="https://hooks.example.test/off",
        secret="",
        fail_count=0,
    )
    monkeypatch.setattr(
        "memchentic.services.memory_webhooks.instance_settings_service.get_webhook_settings",
        AsyncMock(return_value=disabled),
    )
    bump = AsyncMock()
    monkeypatch.setattr(
        "memchentic.services.memory_webhooks.instance_settings_service.increment_webhook_fail_count",
        bump,
    )
    await _run_delivery({"event": "memory", "id": str(uuid.uuid4())})
    deliver.assert_not_awaited()
    bump.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_delivery_failure_bumps_fail_count(monkeypatch: pytest.MonkeyPatch) -> None:
    deliver = _patch_run_delivery_db(monkeypatch)
    deliver.return_value = False
    enabled = instance_settings_service.WebhookSettings(
        enabled=True,
        url="https://hooks.example.test/fail",
        secret="",
        fail_count=2,
    )
    monkeypatch.setattr(
        "memchentic.services.memory_webhooks.instance_settings_service.get_webhook_settings",
        AsyncMock(return_value=enabled),
    )
    bump = AsyncMock()
    monkeypatch.setattr(
        "memchentic.services.memory_webhooks.instance_settings_service.increment_webhook_fail_count",
        bump,
    )
    await _run_delivery({"event": "memory", "id": str(uuid.uuid4())})
    deliver.assert_awaited_once()
    bump.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_delivery_success_no_fail_bump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deliver = _patch_run_delivery_db(monkeypatch)
    enabled = instance_settings_service.WebhookSettings(
        enabled=True,
        url="https://hooks.example.test/ok",
        secret="s",
        fail_count=0,
    )
    monkeypatch.setattr(
        "memchentic.services.memory_webhooks.instance_settings_service.get_webhook_settings",
        AsyncMock(return_value=enabled),
    )
    bump = AsyncMock()
    monkeypatch.setattr(
        "memchentic.services.memory_webhooks.instance_settings_service.increment_webhook_fail_count",
        bump,
    )
    await _run_delivery({"event": "memory", "id": str(uuid.uuid4())})
    deliver.assert_awaited_once()
    bump.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_deliver_without_secret_header() -> None:
    captured: dict[str, Any] = {}

    class _Resp:
        def raise_for_status(self) -> None:
            return None

    async def _post(url: str, *, content: bytes, headers: dict[str, str]) -> _Resp:
        captured["url"] = url
        captured["content"] = content
        captured["headers"] = headers
        return _Resp()

    mock_client = AsyncMock()
    mock_client.post = _post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    with patch("memchentic.services.memory_webhooks.httpx.AsyncClient", return_value=mock_client):
        ok = await _deliver_once({"x": 1}, "https://hooks.example.test/plain", "")
    assert ok is True
    assert "X-Memchentic-Signature" not in captured["headers"]


@pytest.mark.asyncio
async def test_append_event_log_invalid_memory_id(session: AsyncSession) -> None:
    event = MemoryEvent(
        action="updated",
        id="not-a-uuid",
        space_id=str(uuid.uuid4()),
        kind="entry",
        type="note",
        title="t",
        tags=[],
        snippet="s",
    )
    await append_memory_event(session, event)
    await session.rollback()


@pytest.mark.asyncio
async def test_list_event_log_space_filter(
    client: TestClient, pooled_session: AsyncSession
) -> None:
    auth = register_user(client).headers
    space_id = create_group(client, auth, prefix="evfilt")
    created = client.post(
        "/api/memories",
        headers=auth,
        json={
            "title": f"Space filter {uuid.uuid4().hex[:8]}",
            "body": "scoped log row",
            "type": "note",
            "space_id": space_id,
            "force": True,
        },
    )
    assert created.status_code == 200, created.text
    user = client.get("/api/me", headers=auth).json()
    principal = Principal(
        user_id=uuid.UUID(user["id"]),
        is_admin=False,
        email=user["email"],
        display_name=user.get("display_name"),
    )
    await set_rls_context(pooled_session, user_id=principal.user_id, is_admin=False)
    rows = await list_memory_event_log(
        pooled_session,
        principal,
        space_id=uuid.UUID(space_id),
        limit=20,
    )
    assert any(row["memory_id"] == created.json()["id"] for row in rows)


@pytest.mark.asyncio
async def test_export_service_paginates(monkeypatch: pytest.MonkeyPatch) -> None:
    from memchentic.services import memories

    first = MagicMock()
    first.id = uuid.uuid4()
    second = MagicMock()
    second.id = uuid.uuid4()
    store = [first, second]

    async def fake_browse(
        _session: AsyncSession,
        _principal: Principal,
        **kwargs: Any,
    ) -> list[MagicMock]:
        offset = kwargs.get("offset", 0)
        page = get_settings().browse_default_limit
        return store[offset : offset + page]

    monkeypatch.setattr(memories, "browse", fake_browse)
    monkeypatch.setattr(
        "memchentic.services.memory_export.get_settings",
        lambda: type("S", (), {"browse_default_limit": 1})(),
    )
    monkeypatch.setattr(
        "memchentic.services.memory_export.memory_to_dict",
        lambda memory, full=False: {"id": str(memory.id), "full": full},
    )
    principal = Principal(
        user_id=uuid.uuid4(),
        is_admin=False,
        email="a@example.com",
        display_name="a",
    )
    rows = [row async for row in export_memories(MagicMock(), principal)]
    assert {row["id"] for row in rows} == {str(first.id), str(second.id)}


@pytest.mark.asyncio
async def test_export_service_yields_rows(pooled_session: AsyncSession, client: TestClient) -> None:
    auth = register_user(client).headers
    resp = client.post(
        "/api/memories",
        headers=auth,
        json={
            "title": f"Svc export {uuid.uuid4().hex[:8]}",
            "body": "async iterator",
            "type": "note",
            "force": True,
        },
    )
    assert resp.status_code == 200, resp.text
    user = client.get("/api/me", headers=auth).json()
    principal = Principal(
        user_id=uuid.UUID(user["id"]),
        is_admin=False,
        email=user["email"],
        display_name=user.get("display_name"),
    )
    await set_rls_context(pooled_session, user_id=principal.user_id, is_admin=False)
    out = [row async for row in export_memories(pooled_session, principal)]
    assert any(row["id"] == resp.json()["id"] for row in out)


def test_ingest_heuristic_empty_and_headings() -> None:
    assert memory_ingest.suggest_from_text("") == []
    assert memory_ingest.suggest_from_text("   \n\n  ") == []
    bold = memory_ingest.suggest_from_text("**Escalation policy**\n\nRoute tier-2 tickets.")
    assert bold[0]["kind"] == "guide"
    assert bold[0]["title"] == "Escalation policy"
    colon = memory_ingest.suggest_from_text("Rollback:\nRestore the prior image.")
    assert colon[0]["title"] == "Rollback"


@pytest.mark.asyncio
async def test_feedback_scores_aggregate(pooled_session: AsyncSession, client: TestClient) -> None:
    assert await feedback_scores_for_ids(pooled_session, []) == {}
    auth = register_user(client).headers
    created = client.post(
        "/api/memories",
        headers=auth,
        json={
            "title": f"Score {uuid.uuid4().hex[:8]}",
            "body": "feedback aggregate",
            "type": "note",
            "force": True,
        },
    )
    assert created.status_code == 200, created.text
    mid = uuid.UUID(created.json()["id"])
    user = client.get("/api/me", headers=auth).json()
    principal = Principal(
        user_id=uuid.UUID(user["id"]),
        is_admin=False,
        email=user["email"],
        display_name=user.get("display_name"),
    )
    await set_rls_context(pooled_session, user_id=principal.user_id, is_admin=False)
    await submit_feedback(pooled_session, principal, mid, "useful")
    scores = await feedback_scores_for_ids(pooled_session, [mid])
    assert scores[mid] == 1.0


@pytest.mark.asyncio
async def test_auto_apply_consolidation_pass(session: AsyncSession) -> None:
    settings = get_settings()
    admin_user = await ensure_admin_user(session, settings)
    principal = Principal(
        user_id=admin_user.id,
        is_admin=True,
        email=admin_user.email,
        display_name=admin_user.display_name,
    )
    await set_rls_context(session, user_id=principal.user_id, is_admin=True)
    try:
        await set_automation_settings(
            session,
            principal,
            resolve_supersedes=False,
            resolve_near_dups=False,
            auto_apply_consolidations=True,
        )
        marker = uuid.uuid4().hex[:8]
        title = f"Consol {marker}"
        older = await memory_service.remember(
            session, principal, title=title, body="older body", entry_type="note", force=True
        )
        newer = await memory_service.remember(
            session, principal, title=title, body="newer body", entry_type="note", force=True
        )
        await set_rls_context(session, user_id=principal.user_id, is_admin=True)
        counts = await run_hygiene_automation(session)
        assert counts.get("consolidations_applied", 0) >= 1
        await set_rls_context(session, user_id=principal.user_id, is_admin=True)
        assert await memory_service.open_memory(session, principal, memory_id=older.id) is None
        assert await memory_service.open_memory(session, principal, memory_id=newer.id) is not None
    finally:
        await set_rls_context(session, user_id=principal.user_id, is_admin=True)
        await set_automation_settings(
            session,
            principal,
            resolve_supersedes=False,
            resolve_near_dups=False,
            propose_consolidations=False,
            auto_apply_consolidations=False,
        )


@pytest.mark.asyncio
async def test_increment_webhook_fail_count_commits(session: AsyncSession) -> None:
    settings = get_settings()
    admin_user = await ensure_admin_user(session, settings)
    principal = Principal(
        user_id=admin_user.id,
        is_admin=True,
        email=admin_user.email,
        display_name=admin_user.display_name,
    )
    await set_rls_context(session, user_id=principal.user_id, is_admin=True)
    try:
        await set_webhook_settings(
            session,
            principal,
            enabled=True,
            url="https://hooks.example.test/counter",
        )
        await set_rls_context(session, user_id=principal.user_id, is_admin=True)
        await increment_webhook_fail_count(session)
    finally:
        await set_rls_context(session, user_id=principal.user_id, is_admin=True)
        await set_webhook_settings(
            session,
            principal,
            enabled=False,
            url="",
            clear_secret=True,
        )


@pytest.mark.asyncio
async def test_list_history_not_found_raises(session: AsyncSession) -> None:
    from memchentic.services.errors import NotFoundError

    settings = get_settings()
    admin_user = await ensure_admin_user(session, settings)
    principal = Principal(
        user_id=admin_user.id,
        is_admin=True,
        email=admin_user.email,
        display_name=admin_user.display_name,
    )
    await set_rls_context(session, user_id=principal.user_id, is_admin=True)
    with pytest.raises(NotFoundError):
        await list_history(session, principal, uuid.uuid4())
