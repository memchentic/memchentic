from typing import Any
from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
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


async def advisory_xact_lock(session: AsyncSession, key: str) -> None:
    """Take a transaction-scoped Postgres advisory lock named by ``key``.

    Blocks until no other transaction holds the same key. Postgres releases
    the lock when this transaction commits or rolls back, so there is no
    unlock call. Use it to serialise a check-then-write sequence.

    Args:
        session: Database session; the lock belongs to its current transaction.
        key: Stable lock name, for example ``memchentic:dedup:<space id>:guide``.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(CAST(:key AS text), 0))"),
        {"key": key},
    )


async def _reconcile_app_role_password(conn: AsyncConnection, *, role: str, password: str) -> None:
    """Make the app role's password match ``POSTGRES_APP_PASSWORD``.

    Migration 011 sets the password only once. Without this step, changing
    ``POSTGRES_APP_PASSWORD`` after the first boot locks the application out.

    Args:
        conn: Owner-role connection inside a transaction.
        role: App role name (``POSTGRES_APP_USER``).
        password: Wanted password. Empty means: leave the role alone.

    Raises:
        RuntimeError: Postgres refused the change. The message never contains
            the password.
    """
    if not password:
        return
    try:
        exists = await conn.scalar(
            text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}
        )
        if not exists:
            # First boot: migration 011 creates the role with this password.
            return
        # ALTER ROLE takes no bind parameters. Postgres quotes the identifier
        # (%I) and the literal (%L); the finished statement goes to the driver
        # untouched, so ':' or '%' in the password is never read as a parameter.
        statement = await conn.scalar(
            text(
                "SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', "
                "CAST(:role AS text), CAST(:password AS text))"
            ),
            {"role": role, "password": password},
        )
        await conn.exec_driver_sql(statement)
    except DBAPIError:
        # The driver error quotes the statement and its parameters, which
        # contain the password. Do not chain it.
        raise RuntimeError(f"Could not set the password of database role {role!r}") from None


async def init_db() -> None:
    """Ensure extensions exist and the app role password is current.

    The schema itself comes from Alembic. Uses the owner DSN because
    ``CREATE EXTENSION`` and ``ALTER ROLE`` require a superuser / owner and
    must run before the runtime app role connects.
    """
    settings = get_settings()
    engine = get_engine(migration=True)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
            await _reconcile_app_role_password(
                conn,
                role=settings.postgres_app_user.strip() or "memchentic_app",
                password=settings.postgres_app_password,
            )
    finally:
        await engine.dispose()
