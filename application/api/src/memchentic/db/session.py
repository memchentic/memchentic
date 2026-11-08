from typing import Any
from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from memchentic.settings import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(*, migration: bool = False) -> AsyncEngine:
    """Return the shared async engine.

    Args:
        migration: When True, connect as the owner role (extensions / Alembic).
            Runtime callers leave this False so RLS applies via ``memchentic_app``.
    """
    global _engine
    if migration:
        settings = get_settings()
        return create_async_engine(
            settings.migration_database_url,
            echo=False,
            pool_pre_ping=True,
            connect_args={"ssl": settings.postgres_ssl},
        )
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_pre_ping=True,
            connect_args={"ssl": settings.postgres_ssl},
        )
        event.listen(_engine.sync_engine, "checkin", _reset_rls_on_checkin)
    return _engine


def _reset_rls_on_checkin(dbapi_connection: Any, _record: Any) -> None:
    """Clear tenant GUCs so a pooled connection never carries a prior principal.

    Args:
        dbapi_connection: Raw DBAPI connection being returned to the pool; None when
            the connection was invalidated.
        _record: SQLAlchemy connection pool record (unused).
    """
    if dbapi_connection is None:
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute(
            "SELECT set_config('app.user_id', '', false), "
            "set_config('app.is_admin', 'false', false)"
        )
    finally:
        cursor.close()
    dbapi_connection.commit()


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


def reset_engine() -> None:
    """Drop cached engine/session factory so the next call re-reads settings.

    Used by pytest after redirecting to ``memchentic_test``. Safe to call when no
    engine has been created yet.
    """
    global _engine, _session_factory
    engine = _engine
    _engine = None
    _session_factory = None
    if engine is not None:
        engine.sync_engine.dispose()


async def set_rls_context(
    session: AsyncSession,
    *,
    user_id: UUID | None,
    is_admin: bool,
) -> None:
    """Bind the tenant GUCs for RLS helpers on this session's connection.

    Uses session-level ``set_config`` (third arg false). Pool check-in clears
    the GUCs so the next checkout never inherits a prior principal.
    """
    await session.execute(
        text(
            "SELECT set_config('app.user_id', :uid, false), "
            "set_config('app.is_admin', :admin, false)"
        ),
        {"uid": str(user_id) if user_id else "", "admin": "true" if is_admin else "false"},
    )


async def commit_with_rls(
    session: AsyncSession,
    *,
    user_id: UUID | None,
    is_admin: bool,
) -> None:
    """Commit then re-bind RLS GUCs (commit/check-in clears session-level GUCs)."""
    await session.commit()
    await set_rls_context(session, user_id=user_id, is_admin=is_admin)


async def init_db() -> None:
    """Ensure extensions exist; schema comes from Alembic.

    Uses the owner DSN because ``CREATE EXTENSION`` requires a superuser / owner
    and must run before the runtime app role connects.
    """
    engine = get_engine(migration=True)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
    finally:
        await engine.dispose()
