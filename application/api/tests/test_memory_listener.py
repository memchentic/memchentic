"""LISTEN connection: reconnect after a loss, and health reporting."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

import memchentic.events.memory_bus as memory_bus
from memchentic.settings import get_settings


def _token(client: TestClient) -> str:
    email = f"bus{uuid.uuid4().hex[:10]}@example.com"
    password = f"pw-{uuid.uuid4().hex}"
    client.post("/api/auth/register", json={"email": email, "password": password})
    token: str = client.post("/api/auth/login", json={"email": email, "password": password}).json()[
        "access_token"
    ]
    return token


def _terminate_backend(pid: int) -> bool:
    """Terminate one Postgres backend as the owner role."""
    settings = get_settings()

    async def run() -> bool:
        owner = await asyncpg.connect(
            user=settings.postgres_user,
            password=settings.postgres_password,
            host=settings.postgres_hostname,
            port=settings.postgres_port,
            database=settings.postgres_db,
        )
        try:
            return bool(await owner.fetchval("SELECT pg_terminate_backend($1)", pid))
        finally:
            await owner.close()

    return asyncio.run(run())


async def _await_new_listener(old_pid: int) -> int:
    """Yield to the app loop until the supervisor has a fresh listening connection."""
    async with asyncio.timeout(10):
        while True:
            conn = memory_bus._listener_conn
            if (
                conn is not None
                and not conn.is_closed()
                and conn.get_server_pid() != old_pid
                and memory_bus.memory_listener_state() == "listening"
            ):
                pid: int = conn.get_server_pid()
                return pid
            await asyncio.sleep(0.005)


async def _next_event_for(queue: asyncio.Queue[dict[str, Any]], memory_id: str) -> dict[str, Any]:
    async with asyncio.timeout(5):
        while True:
            event = await queue.get()
            if event.get("id") == memory_id:
                return event


def test_listener_reconnects_after_its_backend_is_terminated(client: TestClient) -> None:
    assert client.get("/api/health").json()["events"] == "listening"
    old_conn = memory_bus._listener_conn
    assert old_conn is not None
    old_pid = old_conn.get_server_pid()

    assert _terminate_backend(old_pid) is True

    assert client.portal is not None
    new_pid = client.portal.call(_await_new_listener, old_pid)
    assert new_pid != old_pid
    assert client.get("/api/health").json()["events"] == "listening"

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=8)
    memory_bus._subscribers.add(queue)
    try:
        created = client.post(
            "/api/memories",
            headers={"Authorization": f"Bearer {_token(client)}"},
            json={
                "title": f"after reconnect {uuid.uuid4().hex[:8]}",
                "body": "x",
                "type": "note",
                "force": True,
            },
        )
        assert created.status_code == 200, created.text
        found = client.portal.call(_next_event_for, queue, created.json()["id"])
        assert found["action"] == "created"
    finally:
        memory_bus._subscribers.discard(queue)


def test_health_is_degraded_while_the_listener_reconnects(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(memory_bus, "_listener_state", "reconnecting")

    body = client.get("/api/health").json()

    assert body["events"] == "reconnecting"
    assert body["status"] == "degraded"


def test_health_reports_stopped_without_a_supervisor(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(memory_bus, "_listener_task", None)

    body = client.get("/api/health").json()

    assert body["events"] == "stopped"
    assert body["status"] == "degraded"
