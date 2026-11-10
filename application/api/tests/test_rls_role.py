"""Runtime DB role must not bypass RLS."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from memchentic.db.session import set_rls_context
from memchentic.models.entities import (
    EntryType,
    Memory,
    MemoryKind,
    Space,
    SpaceKind,
    SpaceMember,
    User,
)
from memchentic.settings import get_settings


async def test_runtime_role_is_not_superuser() -> None:
    """The app DSN connects as a non-superuser without BYPASSRLS."""
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
                )
            ).one()
        assert row.rolsuper is False
        assert row.rolbypassrls is False
    finally:
        await engine.dispose()


async def test_rls_hides_foreign_space_without_membership(
    session: AsyncSession,
) -> None:
    """Raw SELECT under a stranger's RLS context cannot see another personal space."""
    settings = get_settings()

    marker = uuid4().hex[:8]
    owner = User(
        id=uuid4(),
        email=f"rls-owner-{marker}@example.com",
        display_name="RLS Owner",
        provider="internal",
        provider_subject=f"rls-owner-{marker}@example.com",
    )
    stranger = User(
        id=uuid4(),
        email=f"rls-stranger-{marker}@example.com",
        display_name="RLS Stranger",
        provider="internal",
        provider_subject=f"rls-stranger-{marker}@example.com",
    )
    session.add_all([owner, stranger])
    await session.flush()
    space = Space(
        id=uuid4(),
        kind=SpaceKind.personal,
        name="personal",
        created_by_user_id=owner.id,
    )
    session.add(space)
    await session.flush()
    session.add(SpaceMember(space_id=space.id, user_id=owner.id, accepted_at=datetime.now(UTC)))
    await session.commit()

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as app_session:
            await set_rls_context(app_session, user_id=stranger.id, is_admin=False)
            visible = await app_session.scalar(
                text("SELECT COUNT(*) FROM spaces WHERE id = :sid"),
                {"sid": space.id},
            )
        assert int(visible or 0) == 0
    finally:
        await engine.dispose()


async def test_rls_hides_group_memories_from_pending_member(session: AsyncSession) -> None:
    """A pending invite grants no row access; accepting touches only accepted_at."""
    settings = get_settings()

    marker = uuid4().hex[:8]
    owner = User(
        id=uuid4(),
        email=f"inv-owner-{marker}@example.com",
        provider="internal",
        provider_subject=f"inv-owner-{marker}@example.com",
    )
    invitee = User(
        id=uuid4(),
        email=f"inv-guest-{marker}@example.com",
        provider="internal",
        provider_subject=f"inv-guest-{marker}@example.com",
    )
    session.add_all([owner, invitee])
    await session.flush()
    group = Space(id=uuid4(), kind=SpaceKind.group, name="g", created_by_user_id=owner.id)
    other = Space(id=uuid4(), kind=SpaceKind.group, name="o", created_by_user_id=owner.id)
    session.add_all([group, other])
    await session.flush()
    session.add_all(
        [
            SpaceMember(space_id=group.id, user_id=owner.id, accepted_at=datetime.now(UTC)),
            SpaceMember(space_id=group.id, user_id=invitee.id),
            Memory(
                id=uuid4(),
                space_id=group.id,
                kind=MemoryKind.entry,
                entry_type=EntryType.note,
                title="t",
                body="b",
                created_by_user_id=owner.id,
            ),
        ]
    )
    await session.commit()

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    count_sql = text("SELECT COUNT(*) FROM memories WHERE space_id = :sid")
    try:
        async with factory() as app_session:
            await set_rls_context(app_session, user_id=invitee.id, is_admin=False)
            assert int(await app_session.scalar(count_sql, {"sid": group.id}) or 0) == 0
            await app_session.execute(
                text(
                    "UPDATE space_members SET accepted_at = now() "
                    "WHERE space_id = :sid AND user_id = :uid"
                ),
                {"sid": group.id, "uid": invitee.id},
            )
            assert int(await app_session.scalar(count_sql, {"sid": group.id}) or 0) == 1
            with pytest.raises(DBAPIError):
                await app_session.execute(
                    text(
                        "UPDATE space_members SET space_id = :other "
                        "WHERE space_id = :sid AND user_id = :uid"
                    ),
                    {"other": other.id, "sid": group.id, "uid": invitee.id},
                )
            await app_session.rollback()
    finally:
        await engine.dispose()


async def test_migration_018_schema_and_creator_membership(session: AsyncSession) -> None:
    """018 adds the consent and editor columns, narrows UPDATE, and accepts creators."""
    settings = get_settings()
    app_user = settings.postgres_app_user or "memchentic_app"
    columns = {
        (row.table_name, row.column_name)
        for row in await session.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE (table_name = 'space_members' AND column_name = 'accepted_at') "
                "OR (table_name = 'memories' AND column_name = 'updated_by_user_id')"
            )
        )
    }
    assert columns == {("space_members", "accepted_at"), ("memories", "updated_by_user_id")}
    privileges = (
        await session.execute(
            text(
                "SELECT has_table_privilege(:role, 'space_members', 'UPDATE') AS table_update, "
                "has_column_privilege(:role, 'space_members', 'accepted_at', 'UPDATE') "
                "AS accepted_update, "
                "has_column_privilege(:role, 'space_members', 'space_id', 'UPDATE') "
                "AS space_update"
            ),
            {"role": app_user},
        )
    ).one()
    assert privileges.table_update is False
    assert privileges.accepted_update is True
    assert privileges.space_update is False

    marker = uuid4().hex[:8]
    creator = User(
        id=uuid4(),
        email=f"mig-creator-{marker}@example.com",
        provider="internal",
        provider_subject=f"mig-creator-{marker}@example.com",
    )
    session.add(creator)
    await session.flush()
    space_id = uuid4()
    await set_rls_context(session, user_id=creator.id, is_admin=False)
    await session.execute(
        text("SELECT app_insert_space(:id, CAST('group' AS space_kind), 'g', :created_by)"),
        {"id": space_id, "created_by": creator.id},
    )
    accepted = await session.scalar(
        text("SELECT accepted_at FROM space_members WHERE space_id = :sid AND user_id = :uid"),
        {"sid": space_id, "uid": creator.id},
    )
    assert accepted is not None
    await session.rollback()
