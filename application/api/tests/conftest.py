"""Pytest isolation: all DB writes go to ``memchentic_test``, never live ``memchentic``.

HTTP integration suites use an in-process FastAPI app (TestClient + lifespan) bound
to that same test database (``POSTGRES_DB=memchentic_test``). External
``MEMCHENTIC_TEST_URL`` targeting the running Compose app is refused so agents
cannot re-pollute the GUI database.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from memchentic.auth import passwords
from memchentic.auth.rate_limit import reset_rate_limits
from memchentic.db.migrate import run_migrations
from memchentic.db.session import reset_engine, set_rls_context
from memchentic.settings import get_settings

_LIVE_DB_NAME = "memchentic"
_TEST_DB_NAME = "memchentic_test"
_SAFE_DB = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _apply_postgres_url(url: str) -> None:
    """Write ``POSTGRES_*`` env pieces from a SQLAlchemy URL.

    Args:
        url: Async Postgres URL to split into discrete env vars.
    """
    parsed = make_url(url)
    if parsed.username is not None:
        os.environ["POSTGRES_USER"] = parsed.username
    if parsed.password is not None:
        os.environ["POSTGRES_PASSWORD"] = parsed.password
    if parsed.host is not None:
        os.environ["POSTGRES_HOSTNAME"] = parsed.host
    if parsed.port is not None:
        os.environ["POSTGRES_PORT"] = str(parsed.port)
    if parsed.database is not None:
        os.environ["POSTGRES_DB"] = parsed.database
    os.environ.pop("DATABASE_URL", None)


def _assert_isolated(url: str) -> None:
    """Refuse to run pytest against the live application database.

    Args:
        url: Candidate SQLAlchemy URL.

    Raises:
        RuntimeError: When the URL targets the live ``memchentic`` database.
    """
    name = make_url(url).database
    if name == _LIVE_DB_NAME:
        raise RuntimeError(
            "Refusing to run pytest against the live database "
            f"{_LIVE_DB_NAME!r}. Use memchentic_test (default) or set "
            "MEMCHENTIC_TEST_DATABASE_URL to a dedicated database."
        )
    if name is None or not _SAFE_DB.match(name):
        raise RuntimeError(f"Unsafe or missing test database name: {name!r}")


def pytest_configure(config: pytest.Config) -> None:
    """Point ``POSTGRES_DB`` at the isolated test database before imports run.

    Args:
        config: Pytest config (unused; hook exists for env side effects).
    """
    del config
    # Production cost (12) takes ~250 ms per hash or verify and dominates suite time.
    passwords.BCRYPT_ROUNDS = 4
    if os.environ.get("MEMCHENTIC_TEST_URL") and os.environ.get("MEMCHENTIC_ALLOW_LIVE_DB") != "1":
        raise RuntimeError(
            "MEMCHENTIC_TEST_URL would hit the running app's live database. "
            "HTTP tests use an in-process app against memchentic_test. "
            "Unset MEMCHENTIC_TEST_URL, or set MEMCHENTIC_ALLOW_LIVE_DB=1 only "
            "if you intentionally accept live pollution."
        )
    override = os.environ.get("MEMCHENTIC_TEST_DATABASE_URL", "").strip()
    if override:
        _apply_postgres_url(override)
    else:
        os.environ["POSTGRES_DB"] = _TEST_DB_NAME
        os.environ.pop("DATABASE_URL", None)
    # Encrypt-at-rest migrations / signing keys need a stable non-weak key.
    if len(os.environ.get("ENCRYPTION_KEY", "")) < 32:
        os.environ["ENCRYPTION_KEY"] = "pytest-memchentic-encryption-key-01"
    # Tests may run without ONNX files; hash fallback is allowed in pytest only.
    os.environ.setdefault("ALLOW_HASH_EMBEDDINGS", "true")
    # The in-process client speaks http://testserver. Pin a loopback http
    # BASE_URL so cookies are issued without Secure and round-trip through the
    # client's cookie jar, whatever BASE_URL the container environment carries.
    os.environ["BASE_URL"] = "http://127.0.0.1:8080"
    # Strong owner + app role passwords so insecure_secret_names() and RLS role
    # creation succeed under pytest (Compose .env may still be placeholders).
    if (
        len(os.environ.get("POSTGRES_PASSWORD", "")) < 16
        or "change-me" in os.environ.get("POSTGRES_PASSWORD", "").casefold()
        or os.environ.get("POSTGRES_PASSWORD") == "memchentic"
    ):
        os.environ["POSTGRES_PASSWORD"] = "pytest-postgres-owner-pw-01"
    if len(os.environ.get("POSTGRES_APP_PASSWORD", "")) < 16:
        os.environ["POSTGRES_APP_PASSWORD"] = "pytest-postgres-app-pw-01"
    if not os.environ.get("POSTGRES_APP_USER"):
        os.environ["POSTGRES_APP_USER"] = "memchentic_app"
    if (
        len(os.environ.get("ADMIN_PASSWORD", "")) < 16
        or "change-me" in os.environ.get("ADMIN_PASSWORD", "").casefold()
    ):
        os.environ["ADMIN_PASSWORD"] = "pytest-admin-password-01"
    if (
        len(os.environ.get("ADMIN_API_KEY", "")) < 16
        or "change-me" in os.environ.get("ADMIN_API_KEY", "").casefold()
    ):
        os.environ["ADMIN_API_KEY"] = "pytest-admin-api-key-01"
    get_settings.cache_clear()
    test_url = get_settings().migration_database_url
    _assert_isolated(test_url)
    reset_engine()


async def _ensure_test_database(url: str) -> None:
    """Drop and recreate the test database so every run starts empty.

    Rows left by earlier runs slow every query; a reused database made the
    suite several times slower.

    Args:
        url: Async URL whose database name should be recreated.
    """
    parsed = make_url(url)
    assert parsed.database is not None
    _assert_isolated(url)
    admin_url = parsed.set(database="postgres").render_as_string(hide_password=False)
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{parsed.database}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{parsed.database}"'))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def prepare_test_database() -> None:
    """Recreate ``memchentic_test`` and migrate it once per pytest process."""
    owner_url = get_settings().migration_database_url
    _assert_isolated(owner_url)
    asyncio.run(_ensure_test_database(owner_url))
    get_settings.cache_clear()
    reset_engine()
    assert owner_url == get_settings().migration_database_url
    run_migrations()


@pytest.fixture(autouse=True)
def _clear_auth_rate_limits() -> None:
    """Avoid cross-test 429s on login/register/token buckets."""
    reset_rate_limits()


@pytest.fixture(scope="session")
def client() -> Iterator[TestClient]:
    """In-process HTTP client against the app bound to ``memchentic_test``.

    Yields:
        A ``TestClient`` with lifespan started (migrations + MCP).
    """
    _assert_isolated(get_settings().database_url)
    reset_engine()
    get_settings.cache_clear()
    # Import after DATABASE_URL rewrite so module-level wiring sees the test DB.
    from memchentic.main import create_app

    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Admin-scoped session against the isolated test database.

    Yields:
        An ``AsyncSession`` with RLS admin context set.
    """
    settings = get_settings()
    _assert_isolated(settings.database_url)
    engine = create_async_engine(settings.database_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        await set_rls_context(db, user_id=None, is_admin=True)
        yield db
    await engine.dispose()
