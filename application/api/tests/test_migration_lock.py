"""Boot DDL (extensions and Alembic) waits for the migration advisory lock."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from sqlalchemy import text

from memchentic.db.session import MIGRATION_LOCK_ID, get_engine, init_db

_WAITERS = text(
    "SELECT count(*) FROM pg_locks "
    "WHERE locktype = 'advisory' AND classid = 0 "
    "AND CAST(objid AS bigint) = :k AND NOT granted"
)


def _alembic_upgrade_head() -> None:
    # The application's own entry point. Its config sets
    # ``attributes["configure_logger"] = False`` (BUG-006), so this run does
    # not reconfigure logging for the tests that follow.
    from memchentic.main import _run_migrations

    _run_migrations()


async def _assert_waits_for_lock(start: Callable[[], Awaitable[None]]) -> None:
    """Hold the lock on one connection and prove that ``start()`` blocks on it."""
    engine = get_engine(migration=True)
    try:
        async with engine.connect() as holder, engine.connect() as observer:
            await holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MIGRATION_LOCK_ID})
            task = asyncio.ensure_future(start())
            try:
                waiting = 0
                for _ in range(200):
                    waiting = await observer.scalar(_WAITERS, {"k": MIGRATION_LOCK_ID})
                    if waiting or task.done():
                        break
                    await asyncio.sleep(0.05)
                assert waiting == 1, "boot DDL ran without waiting for the advisory lock"
                assert not task.done()
            finally:
                await holder.execute(
                    text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK_ID}
                )
            await asyncio.wait_for(task, timeout=60)
    finally:
        await engine.dispose()


async def test_alembic_upgrade_waits_for_migration_lock() -> None:
    await _assert_waits_for_lock(lambda: asyncio.to_thread(_alembic_upgrade_head))


async def test_init_db_waits_for_migration_lock() -> None:
    await _assert_waits_for_lock(init_db)
