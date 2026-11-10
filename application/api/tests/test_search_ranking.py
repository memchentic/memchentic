"""Ranking regression: an old exact-title match stays the first hit."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import asyncpg
from fastapi.testclient import TestClient

from memchentic.settings import get_settings


def _user(client: TestClient) -> dict[str, str]:
    email = f"rank{uuid.uuid4().hex[:10]}@example.com"
    password = f"pw-{uuid.uuid4().hex}"
    client.post("/api/auth/register", json={"email": email, "password": password})
    token = client.post("/api/auth/login", json={"email": email, "password": password}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {token}"}


def _tool(client: TestClient, auth: dict[str, str], name: str, args: dict[str, Any]) -> Any:
    resp = client.post(
        "/mcp",
        headers={**auth, "Accept": "application/json, text/event-stream"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        },
    )
    resp.raise_for_status()
    return resp.json()["result"]["structuredContent"]


def _age_90_days(memory_id: str) -> None:
    """Move created_at and updated_at back 90 days (owner role, bypasses RLS)."""
    settings = get_settings()

    async def run() -> None:
        conn = await asyncpg.connect(
            user=settings.postgres_user,
            password=settings.postgres_password,
            host=settings.postgres_hostname,
            port=settings.postgres_port,
            database=settings.postgres_db,
        )
        try:
            await conn.execute(
                "UPDATE memories SET updated_at = now() - interval '90 days', "
                "created_at = now() - interval '90 days' WHERE id = $1",
                uuid.UUID(memory_id),
            )
        finally:
            await conn.close()

    asyncio.run(run())


def test_old_exact_title_match_is_the_first_hit(client: TestClient) -> None:
    auth = _user(client)
    marker = uuid.uuid4().hex[:8]
    title = f"Postgres failover runbook {marker}"
    target = client.post(
        "/api/memories",
        headers=auth,
        json={
            "title": title,
            "body": f"Promote the standby then repoint pgbouncer {marker}",
            "type": "fact",
            "force": True,
        },
    ).json()
    for i in range(25):
        resp = client.post(
            "/api/memories",
            headers=auth,
            json={
                "title": f"note {i} {uuid.uuid4().hex[:6]}",
                "body": f"unrelated scratch text number {i} about lunch and weather",
                "type": "note",
                "force": True,
            },
        )
        assert resp.status_code == 200, resp.text
    _age_90_days(target["id"])

    hits = _tool(client, auth, "find", {"query": title})["result"]

    assert hits, "find returned no hits"
    assert hits[0]["id"] == target["id"]
