"""Schema gates: the ORM metadata must match the migrated database."""

from __future__ import annotations

import warnings
from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, func, select, text
from sqlalchemy.exc import SAWarning
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from memchentic.models import Base, Memory
from memchentic.settings import get_settings


def _diff(connection: Connection) -> list[Any]:
    """Return the operations Alembic autogenerate would emit.

    Args:
        connection: Synchronous connection to the migrated test database.

    Returns:
        The autogenerate diff between ``Base.metadata`` and the live schema.
    """
    return list(compare_metadata(MigrationContext.configure(connection), Base.metadata))


async def test_orm_metadata_matches_migrated_schema() -> None:
    """Same comparison as ``alembic check``, against the migrated test database."""
    engine = create_async_engine(get_settings().migration_database_url)
    try:
        async with engine.connect() as conn:
            diff = await conn.run_sync(_diff)
    finally:
        await engine.dispose()
    assert diff == [], f"ORM models and migrations disagree: {diff!r}"


def test_metadata_tables_sort_without_cycle_warning() -> None:
    """Sorting the ORM tables emits no foreign-key cycle warning."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", SAWarning)
        names = [table.name for table in Base.metadata.sorted_tables]
    assert "users" in names
    assert "spaces" in names


async def test_lookup_indexes_exist(session: AsyncSession) -> None:
    """Migration 020 created the three lookup indexes with the expected keys."""
    rows = (
        await session.execute(
            text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' AND indexname IN ("
                "'ix_space_members_user_id', "
                "'ix_memories_space_lower_title', "
                "'ix_memories_created_by_user_id')"
            )
        )
    ).all()
    definitions: dict[str, str] = dict(rows)
    assert set(definitions) == {
        "ix_space_members_user_id",
        "ix_memories_space_lower_title",
        "ix_memories_created_by_user_id",
    }
    assert "(user_id)" in definitions["ix_space_members_user_id"]
    assert "lower(btrim((title)::text))" in definitions["ix_memories_space_lower_title"]
    assert "(created_by_user_id)" in definitions["ix_memories_created_by_user_id"]


async def test_title_lookup_plan_uses_title_index(session: AsyncSession) -> None:
    """The application's ``lower(trim(title))`` predicate can use the title index."""
    stmt = select(Memory.id).where(
        Memory.space_id == text("'00000000-0000-0000-0000-000000000000'::uuid"),
        func.lower(func.trim(Memory.title)) == "x",
    )
    compiled = stmt.compile(
        dialect=session.get_bind().dialect, compile_kwargs={"literal_binds": True}
    )
    await session.execute(text("SET LOCAL enable_seqscan = off"))
    plan = "\n".join(
        str(row[0]) for row in (await session.execute(text(f"EXPLAIN {compiled}"))).all()
    )
    await session.rollback()
    assert "ix_memories_space_lower_title" in plan, plan
