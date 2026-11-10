"""Contract tests for the shared fixtures in conftest.py and support.py."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.db.session import commit_with_rls, set_rls_context
from support import register_user, restore_shared_rows


async def _is_admin_setting(db: AsyncSession) -> str | None:
    result = await db.execute(text("SELECT current_setting('app.is_admin', true)"))
    return result.scalar_one()


async def test_app_session_loses_rls_context_on_plain_commit(app_session: AsyncSession) -> None:
    """A plain commit returns the connection to the pool, which clears the RLS settings."""
    await set_rls_context(app_session, user_id=None, is_admin=True)
    assert await _is_admin_setting(app_session) == "true"

    await app_session.commit()
    assert await _is_admin_setting(app_session) != "true"

    await set_rls_context(app_session, user_id=None, is_admin=True)
    await commit_with_rls(app_session, user_id=None, is_admin=True)
    assert await _is_admin_setting(app_session) == "true"


async def test_session_fixture_keeps_admin_context_after_commit(session: AsyncSession) -> None:
    """The arrange-only ``session`` fixture keeps its admin context across commits."""
    await session.commit()
    assert await _is_admin_setting(session) == "true"


def test_client_starts_with_empty_cookie_jar(client: TestClient) -> None:
    """Cookies stored by earlier tests are gone; a login in this test stores one."""
    assert len(client.cookies.jar) == 0
    register_user(client)
    assert len(client.cookies.jar) > 0


def test_restore_shared_rows_reenables_registration(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """A test that disables registration cannot break the tests after it."""
    off = client.put("/api/system/registration", headers=admin_headers, json={"enabled": False})
    assert off.status_code == 200, off.text
    assert client.get("/api/provider").json()["register_enabled"] is False
    with pytest.raises(AssertionError, match="register failed"):
        register_user(client)

    restore_shared_rows()

    assert client.get("/api/provider").json()["register_enabled"] is True
    register_user(client)
