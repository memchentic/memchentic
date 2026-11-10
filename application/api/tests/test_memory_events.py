"""Live memory SSE auth + NOTIFY fan-out tests against memchentic_test."""

from __future__ import annotations

import asyncio
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.events import memory_bus
from memchentic.events.memory_bus import (
    MemoryEvent,
    _encode_event,
    _subscribers,
    publish_memory_event,
)


def _register_and_token(client: TestClient) -> str:
    email = f"e{uuid.uuid4().hex[:10]}@example.com"
    password = f"pw-{uuid.uuid4().hex}"
    client.post("/api/auth/register", json={"email": email, "password": password})
    res = client.post(
        "/api/auth/login",
        json={"email": email, "password": password, "remember": False},
    )
    assert res.status_code == 200
    return res.json()["access_token"]


def _wait_for_event(queue: asyncio.Queue, memory_id: str, *, timeout: float = 3.0) -> dict | None:
    found = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not queue.empty():
            found = queue.get_nowait()
            if found.get("id") == memory_id:
                return found
            found = None
        time.sleep(0.05)
    return None


def test_memory_events_requires_auth(client: TestClient) -> None:
    res = client.get("/api/events/memories")
    assert res.status_code == 401


def test_create_memory_fans_out_local_subscribers(client: TestClient) -> None:
    """REST create NOTIFYs; the process listener delivers to local queues."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=8)
    _subscribers.add(queue)
    try:
        token = _register_and_token(client)
        headers = {"Authorization": f"Bearer {token}"}
        title = f"live-{uuid.uuid4().hex[:8]}"
        create = client.post(
            "/api/memories",
            headers=headers,
            json={"title": title, "body": "appears on the wire", "type": "note", "force": True},
        )
        assert create.status_code == 200
        memory_id = create.json()["id"]
        found = _wait_for_event(queue, memory_id)
        assert found is not None
        assert found["action"] == "created"
        assert found["title"] == title
        assert found.get("created_at")
        assert found.get("created_by") == create.json()["created_by"]
    finally:
        _subscribers.discard(queue)


def test_personal_memory_event_space_not_readable_by_peer(client: TestClient) -> None:
    """The bus carries a peer's personal event; the peer's space list excludes that space."""
    token_a = _register_and_token(client)
    token_b = _register_and_token(client)
    queue: asyncio.Queue = asyncio.Queue(maxsize=8)
    _subscribers.add(queue)
    try:
        create = client.post(
            "/api/memories",
            headers={"Authorization": f"Bearer {token_b}"},
            json={
                "title": f"secret-{uuid.uuid4().hex[:8]}",
                "body": "peer must not see",
                "type": "note",
                "force": True,
            },
        )
        assert create.status_code == 200
        payload = create.json()
        found = _wait_for_event(queue, payload["id"])
        assert found is not None
        assert found["space_id"] == payload["space_id"]

        spaces_a = client.get("/api/spaces", headers={"Authorization": f"Bearer {token_a}"}).json()
        readable = {s["id"] for s in spaces_a}
        assert payload["space_id"] not in readable
    finally:
        _subscribers.discard(queue)


def _oversize_event() -> MemoryEvent:
    return MemoryEvent(
        action="created",
        id=str(uuid.uuid4()),
        space_id=str(uuid.uuid4()),
        kind="entry",
        type="fact",
        # 12 bytes per character once JSON-escaped. Longer than the title column
        # allows, on purpose: it exercises the last reduction step as well.
        title="\U0001f600" * 1000,
        tags=[f"{i:02d}-" + "t" * 97 for i in range(90)],
        snippet="s" * 181,
    )


def test_event_payload_always_fits_notify_limit() -> None:
    """An oversize event is reduced, not rejected; a normal event is unchanged."""
    big = _oversize_event()
    payload = _encode_event(big)
    assert len(payload.encode()) <= 7900
    data = json.loads(payload)
    assert (data["action"], data["id"], data["space_id"]) == ("created", big.id, big.space_id)
    assert data["tags"] == []
    assert data["snippet"] == ""
    assert data["title"] == big.title[:200]

    small = MemoryEvent(
        action="updated",
        id=big.id,
        space_id=big.space_id,
        kind="entry",
        type="fact",
        title="ok",
        tags=["a", "b"],
        snippet="hello",
    )
    assert json.loads(_encode_event(small)) == small.to_dict()


async def test_publish_oversize_event_does_not_raise(session: AsyncSession) -> None:
    """pg_notify accepts the reduced payload (the statement used to raise)."""
    await publish_memory_event(session, _oversize_event())
    await session.rollback()  # never deliver the test event


async def test_close_memory_streams_wakes_waiting_subscriber(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A subscriber blocked on its queue receives the shutdown sentinel."""
    monkeypatch.setattr(memory_bus, "_streams_closing", False)
    async with memory_bus.subscribe_memory_events() as queue:
        waiter = asyncio.create_task(queue.get())
        await asyncio.sleep(0)
        memory_bus.close_memory_streams()
        received = await asyncio.wait_for(waiter, timeout=1.0)
    assert received is memory_bus.STREAM_CLOSED


def test_sse_stream_ends_when_shutdown_has_started(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once shutdown has started, an SSE request completes instead of streaming forever."""
    token = _register_and_token(client)
    close_streams = memory_bus.close_memory_streams
    # monkeypatch restores the flag to False after the test.
    monkeypatch.setattr(memory_bus, "_streams_closing", False)
    assert client.portal is not None
    client.portal.call(close_streams)  # run on the application's event loop

    res = client.get("/api/events/memories", headers={"Authorization": f"Bearer {token}"})

    assert res.status_code == 200
    assert res.text == ": connected\n\n"
