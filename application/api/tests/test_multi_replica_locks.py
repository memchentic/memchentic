"""Bootstrap and hygiene advisory locks used when replicaCount > 1."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from memchentic.db.session import BOOTSTRAP_LOCK_ID, HYGIENE_LOCK_ID, get_engine
from memchentic.services.bootstrap import bootstrap_instance
from memchentic.settings import get_settings


@pytest.mark.asyncio
async def test_bootstrap_holds_session_lock_across_commits(session: AsyncSession) -> None:
    """A second connection blocks on the bootstrap lock until the first unlocks."""
    settings = get_settings()
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)

    async with factory() as holder:
        await holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": BOOTSTRAP_LOCK_ID})
        waiting = asyncio.create_task(bootstrap_instance(session, settings))
        await asyncio.sleep(0.2)
        assert not waiting.done()
        await holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": BOOTSTRAP_LOCK_ID})
        await holder.commit()
        await asyncio.wait_for(waiting, timeout=10)


@pytest.mark.asyncio
async def test_hygiene_try_lock_is_exclusive(session: AsyncSession) -> None:
    """Only one connection can hold the hygiene leader lock at a time."""
    del session
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)

    async with factory() as a, factory() as b:
        got_a = await a.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": HYGIENE_LOCK_ID})
        got_b = await b.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": HYGIENE_LOCK_ID})
        assert got_a is True
        assert got_b is False
        await a.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": HYGIENE_LOCK_ID})
        await a.commit()
        got_b2 = await b.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": HYGIENE_LOCK_ID})
        assert got_b2 is True
        await b.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": HYGIENE_LOCK_ID})
        await b.commit()
