"""reembed_all: every batch is processed and updated_at is kept."""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
from fastapi.testclient import TestClient

from memchentic.db.session import reset_engine
from memchentic.embeddings.reembed import reembed_all
from memchentic.settings import get_settings


def _user(client: TestClient) -> dict[str, str]:
    email = f"reembed{uuid.uuid4().hex[:10]}@example.com"
    password = f"pw-{uuid.uuid4().hex}"
    client.post("/api/auth/register", json={"email": email, "password": password})
    token = client.post("/api/auth/login", json={"email": email, "password": password}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {token}"}


def test_reembed_all_covers_every_batch_and_keeps_updated_at(client: TestClient) -> None:
    """Runtime grows with the number of rows in memchentic_test (all are re-embedded)."""
    auth = _user(client)
    for i in range(3):
        resp = client.post(
            "/api/memories",
            headers=auth,
            json={
                "title": f"reembed seed {i} {uuid.uuid4().hex}",
                "body": "b",
                "type": "note",
                "force": True,
            },
        )
        assert resp.status_code == 200, resp.text
    settings = get_settings()

    async def run() -> tuple[int, int, int]:
        # Owner role: sees every row without an RLS context.
        owner = await asyncpg.connect(
            user=settings.postgres_user,
            password=settings.postgres_password,
            host=settings.postgres_hostname,
            port=settings.postgres_port,
            database=settings.postgres_db,
        )
        try:
            total = await owner.fetchval("SELECT count(*) FROM memories")
            newest = await owner.fetchval("SELECT max(updated_at) FROM memories")
            # reembed_all uses the application engine. Its pooled connections
            # belong to the TestClient event loop, so drop the engine before the
            # call and again after it; the application then builds a new one.
            reset_engine()
            try:
                # Half the table per batch: the loop must survive one commit.
                done = await reembed_all(batch_size=(total + 1) // 2)
            finally:
                reset_engine()
            bumped = await owner.fetchval(
                "SELECT count(*) FROM memories WHERE updated_at > $1", newest
            )
        finally:
            await owner.close()
        return total, done, bumped

    total, done, bumped = asyncio.run(run())

    assert total >= 3
    assert done == total
    assert bumped == 0
    assert client.get("/api/memories", headers=auth).status_code == 200
