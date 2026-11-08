"""Live memory SSE auth + NOTIFY fan-out tests against memchentic_test."""

from __future__ import annotations

import asyncio
import time
import uuid

from fastapi.testclient import TestClient

from memchentic.events.memory_bus import _subscribers


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
    """Bus may carry peer personal events; SSE ACL uses space list — peer must not see that space."""
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

        spaces_a = client.get(
            "/api/spaces", headers={"Authorization": f"Bearer {token_a}"}
        ).json()
        readable = {s["id"] for s in spaces_a}
        assert payload["space_id"] not in readable
    finally:
        _subscribers.discard(queue)
