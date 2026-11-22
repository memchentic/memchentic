"""Bootstrap and hygiene advisory locks used when replicaCount > 1."""

from __future__ import annotations

import asyncio
from urllib.parse import unquote, urlparse

import asyncpg
import pytest

from memchentic.db.session import BOOTSTRAP_LOCK_ID, HYGIENE_LOCK_ID
from memchentic.settings import get_settings


def _dsn_parts() -> dict[str, object]:
    settings = get_settings()
    parsed = urlparse(settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1))
    ssl_arg: object = False
    if settings.postgres_sslmode != "disable":
        ssl_arg = settings.postgres_ssl
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "database": (parsed.path or "/").lstrip("/") or "memchentic",
        "ssl": ssl_arg,
    }


@pytest.fixture
def _client_booted(client: object) -> None:
    """Ensure the app lifespan has applied migrations once."""
    del client


@pytest.mark.usefixtures("_client_booted")
def test_hygiene_try_lock_is_exclusive() -> None:
    """Only one connection can hold the hygiene leader lock at a time."""

    async def _run() -> None:
        dsn = _dsn_parts()
        a = await asyncpg.connect(**dsn)  # type: ignore[arg-type]
        b = await asyncpg.connect(**dsn)  # type: ignore[arg-type]
        try:
            got_a = await a.fetchval("SELECT pg_try_advisory_lock($1)", HYGIENE_LOCK_ID)
            got_b = await b.fetchval("SELECT pg_try_advisory_lock($1)", HYGIENE_LOCK_ID)
            assert got_a is True
            assert got_b is False
            await a.fetchval("SELECT pg_advisory_unlock($1)", HYGIENE_LOCK_ID)
            got_b2 = await b.fetchval("SELECT pg_try_advisory_lock($1)", HYGIENE_LOCK_ID)
            assert got_b2 is True
            await b.fetchval("SELECT pg_advisory_unlock($1)", HYGIENE_LOCK_ID)
        finally:
            await a.close()
            await b.close()

    asyncio.run(_run())


@pytest.mark.usefixtures("_client_booted")
def test_bootstrap_lock_blocks_second_connection() -> None:
    """A second connection waits until the bootstrap session lock is released."""

    async def _run() -> None:
        dsn = _dsn_parts()
        holder = await asyncpg.connect(**dsn)  # type: ignore[arg-type]
        waiter = await asyncpg.connect(**dsn)  # type: ignore[arg-type]
        try:
            await holder.fetchval("SELECT pg_advisory_lock($1)", BOOTSTRAP_LOCK_ID)
            wait_task = asyncio.create_task(
                waiter.fetchval("SELECT pg_advisory_lock($1)", BOOTSTRAP_LOCK_ID)
            )
            await asyncio.sleep(0.2)
            assert not wait_task.done()
            await holder.fetchval("SELECT pg_advisory_unlock($1)", BOOTSTRAP_LOCK_ID)
            await asyncio.wait_for(wait_task, timeout=5)
            await waiter.fetchval("SELECT pg_advisory_unlock($1)", BOOTSTRAP_LOCK_ID)
        finally:
            await holder.close()
            await waiter.close()

    asyncio.run(_run())
