"""init_db keeps the app role password in line with POSTGRES_APP_PASSWORD."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from memchentic.db.session import create_owner_engine, init_db
from memchentic.settings import get_settings


async def _owner_exec(statement: str) -> None:
    engine = create_owner_engine()
    try:
        async with engine.begin() as conn:
            await conn.exec_driver_sql(statement)
    finally:
        await engine.dispose()


async def test_init_db_reconciles_app_role_password(monkeypatch: pytest.MonkeyPatch) -> None:
    # A throwaway role: the real app role is cluster-wide and must not change.
    role = f"memchentic_pwtest_{uuid.uuid4().hex[:12]}"
    new_password = f"rotated-{uuid.uuid4().hex}"
    await _owner_exec(f'CREATE ROLE "{role}" NOLOGIN')
    try:
        monkeypatch.setenv("POSTGRES_APP_USER", role)
        monkeypatch.setenv("POSTGRES_APP_PASSWORD", new_password)
        get_settings.cache_clear()

        await init_db()

        engine = create_async_engine(get_settings().database_url)
        try:
            async with engine.connect() as conn:
                assert await conn.scalar(text("SELECT current_user")) == role
        finally:
            await engine.dispose()
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        await _owner_exec(f'DROP ROLE IF EXISTS "{role}"')
