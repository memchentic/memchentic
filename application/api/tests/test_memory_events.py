"""Live memory SSE auth + NOTIFY fan-out tests against memchentic_test."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

import memchentic.api.routes.events as rest_router
from memchentic.events import memory_bus
from memchentic.events.memory_bus import (
    MemoryEvent,
    _connect_kwargs_from_settings,
    _encode_event,
    publish_memory_event,
)
from memchentic.settings import get_settings
from support import MemoryEventsTap, register_user


def _register_and_token(client: TestClient) -> str:
    return register_user(client, prefix="e").token


def test_memory_events_requires_auth(client: TestClient) -> None:
    res = client.get("/api/events/memories")
    assert res.status_code == 401


def test_create_memory_fans_out_local_subscribers(
    client: TestClient, memory_events_tap: MemoryEventsTap
) -> None:
    """REST create NOTIFYs; the process listener delivers to local subscribers."""
    token = _register_and_token(client)
    headers = {"Authorization": f"Bearer {token}"}
    title = f"live-{uuid.uuid4().hex[:8]}"
    create = client.post(
        "/api/memories",
        headers=headers,
        json={"title": title, "body": "appears on the wire", "type": "note", "force": True},
    )
    assert create.status_code == 200
    found = memory_events_tap.wait_for(create.json()["id"])
    assert found is not None
    assert found["action"] == "created"
    assert found["title"] == title
    assert found.get("created_at")
    assert found.get("created_by") == create.json()["created_by"]


def test_personal_memory_event_space_not_readable_by_peer(
    client: TestClient, memory_events_tap: MemoryEventsTap
) -> None:
    """The bus carries a peer's personal event; the peer's space list excludes that space."""
    token_a = _register_and_token(client)
    token_b = _register_and_token(client)
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
    found = memory_events_tap.wait_for(payload["id"])
    assert found is not None
    assert found["space_id"] == payload["space_id"]

    spaces_a = client.get("/api/spaces", headers={"Authorization": f"Bearer {token_a}"}).json()
    assert payload["space_id"] not in {s["id"] for s in spaces_a}


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


def test_connect_kwargs_preserve_special_password(monkeypatch: pytest.MonkeyPatch) -> None:
    password = "p@ss/w:rd#1"
    monkeypatch.setenv("POSTGRES_APP_USER", "mem")
    monkeypatch.setenv("POSTGRES_APP_PASSWORD", password)
    monkeypatch.setenv("POSTGRES_HOSTNAME", "dbhost")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_DB", "memchentic")
    get_settings.cache_clear()
    try:
        # Settings quote when building the URL; kwargs must unquote cleanly.
        assert quote(password, safe="") in get_settings().database_url
        kwargs = _connect_kwargs_from_settings()
        assert kwargs["user"] == "mem"
        assert kwargs["password"] == password
        assert kwargs["host"] == "dbhost"
        assert kwargs["port"] == 5432
        assert kwargs["database"] == "memchentic"
    finally:
        get_settings.cache_clear()


def test_dsn_round_trips_password_with_space_and_plus(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both consumers of the DSN decode a password with spaces and a plus sign."""
    password = "pass word+plus 123"
    monkeypatch.setenv("POSTGRES_APP_USER", "mem")
    monkeypatch.setenv("POSTGRES_APP_PASSWORD", password)
    monkeypatch.setenv("POSTGRES_HOSTNAME", "dbhost")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_DB", "memchentic")
    get_settings.cache_clear()
    try:
        # Event listener (asyncpg keyword arguments).
        assert _connect_kwargs_from_settings()["password"] == password
        # Main engine (SQLAlchemy URL parser).
        assert make_url(get_settings().database_url).password == password
    finally:
        get_settings.cache_clear()


def _stream_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/events/memories",
            "query_string": b"",
            "headers": [(b"authorization", b"Bearer test-token")],
        }
    )


async def _open_stream(monkeypatch: pytest.MonkeyPatch, reauth: Any) -> tuple[Any, asyncio.Future]:
    """Start the stream and wait until it has subscribed to the bus."""
    subscribers_before = len(memory_bus._subscribers)
    monkeypatch.setattr(rest_router, "_reauth_memory_events", reauth)
    response = await rest_router.memory_events(_stream_request())
    stream = response.body_iterator
    assert await anext(stream) == b": connected\n\n"
    waiting = asyncio.ensure_future(anext(stream))
    for _ in range(200):
        if len(memory_bus._subscribers) > subscribers_before:
            return stream, waiting
        await asyncio.sleep(0)
    waiting.cancel()
    raise AssertionError("stream did not subscribe to the memory bus")


async def _close_stream(stream: Any, waiting: asyncio.Future) -> None:
    waiting.cancel()
    with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
        await waiting
    await stream.aclose()


async def test_stream_omits_events_from_unreadable_space(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only events whose space the caller can read are written to the stream."""

    async def reauth(_token: str | None) -> set[str]:
        return {"space-readable"}

    subscribers_before = len(memory_bus._subscribers)
    stream, waiting = await _open_stream(monkeypatch, reauth)
    try:
        memory_bus._fan_out({"action": "created", "id": "m-hidden", "space_id": "space-other"})
        memory_bus._fan_out({"action": "created", "id": "m-visible", "space_id": "space-readable"})
        chunk = await asyncio.wait_for(waiting, timeout=2)
    finally:
        await _close_stream(stream, waiting)

    assert chunk.startswith(b"event: memory\ndata: ")
    assert json.loads(chunk.decode().split("data: ", 1)[1])["id"] == "m-visible"
    assert len(memory_bus._subscribers) == subscribers_before


async def test_stream_ends_when_token_stops_resolving(monkeypatch: pytest.MonkeyPatch) -> None:
    """When re-authentication fails the stream ends and unsubscribes."""
    calls = 0

    async def reauth(_token: str | None) -> set[str] | None:
        nonlocal calls
        calls += 1
        return {"space-readable"} if calls == 1 else None

    subscribers_before = len(memory_bus._subscribers)
    monkeypatch.setattr(rest_router, "_SSE_REAUTH_SECONDS", 0.0)
    stream, waiting = await _open_stream(monkeypatch, reauth)
    try:
        memory_bus._fan_out({"action": "created", "id": "m-late", "space_id": "space-readable"})
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(waiting, timeout=2)
    finally:
        await _close_stream(stream, waiting)

    assert calls == 2
    assert len(memory_bus._subscribers) == subscribers_before
