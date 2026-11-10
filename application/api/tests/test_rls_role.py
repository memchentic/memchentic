"""Runtime DB role must not bypass RLS."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from memchentic.db.session import set_rls_context
from memchentic.models.entities import Space, SpaceKind, SpaceMember, User
from memchentic.settings import get_settings


@pytest.mark.asyncio
async def test_runtime_role_is_not_superuser() -> None:
    """The app DSN connects as a non-superuser without BYPASSRLS."""
    settings = get_settings()
    if not settings.postgres_app_password:
        pytest.skip("POSTGRES_APP_PASSWORD unset")
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


@pytest.mark.asyncio
async def test_rls_hides_foreign_space_without_membership(
    session: AsyncSession,
) -> None:
    """Raw SELECT under a stranger's RLS context cannot see another personal space."""
    settings = get_settings()
    if not settings.postgres_app_password:
        pytest.skip("POSTGRES_APP_PASSWORD unset")

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
    session.add(SpaceMember(space_id=space.id, user_id=owner.id))
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
