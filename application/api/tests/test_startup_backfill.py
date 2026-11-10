"""Startup orphan backfill must run with an admin RLS context of its own."""

from __future__ import annotations

import asyncio
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from memchentic.auth.users import ensure_admin_user
from memchentic.db.session import _reset_rls_on_checkin, set_rls_context
from memchentic.services.auth_groups import ensure_auth_groups
from memchentic.services.memories import backfill_orphan_creators
from memchentic.settings import get_settings


def _user(client: TestClient) -> tuple[dict[str, str], str]:
    email = f"backfill-{uuid.uuid4().hex[:10]}@example.com"
    password = f"pw-{uuid.uuid4().hex}"
    reg = client.post("/api/auth/register", json={"email": email, "password": password})
    assert reg.status_code == 200, reg.text
    login = client.post("/api/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    auth = {"Authorization": f"Bearer {login.json()['access_token']}"}
    return auth, client.get("/api/me", headers=auth).json()["id"]


def test_startup_backfill_attributes_orphans(client: TestClient) -> None:
    auth, user_id = _user(client)
    created = client.post(
        "/api/memories",
        headers=auth,
        json={"title": f"orphan {uuid.uuid4().hex[:8]}", "body": "x", "force": True},
    )
    assert created.status_code == 200, created.text
    memory_id = uuid.UUID(created.json()["id"])

    async def run() -> tuple[int, uuid.UUID | None]:
        settings = get_settings()
        # Same wiring as the runtime engine: the check-in listener clears the RLS
        # context whenever a commit returns the connection to the pool.
        engine = create_async_engine(settings.database_url)
        event.listen(engine.sync_engine, "checkin", _reset_rls_on_checkin)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                await set_rls_context(session, user_id=None, is_admin=True)
                await session.execute(
                    text("UPDATE memories SET created_by_user_id = NULL WHERE id = :id"),
                    {"id": memory_id},
                )
                await session.commit()
                # Replay the startup calls that run before the backfill.
                await set_rls_context(session, user_id=None, is_admin=True)
                await ensure_admin_user(session, settings)
                await ensure_auth_groups(session, settings)
                updated = await backfill_orphan_creators(session)
                creator = await session.scalar(
                    text("SELECT created_by_user_id FROM memories WHERE id = :id"),
                    {"id": memory_id},
                )
                return updated, creator
        finally:
            await engine.dispose()

    updated, creator = asyncio.run(run())
    assert updated >= 1
    assert creator == uuid.UUID(user_id)
