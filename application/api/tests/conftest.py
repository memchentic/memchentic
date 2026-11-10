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
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import support
from memchentic.auth import passwords
from memchentic.auth.jwks import clear_jwks_cache
from memchentic.auth.providers import clear_discovery_cache
from memchentic.auth.rate_limit import reset_rate_limits
from memchentic.db import session as db_session_module
from memchentic.db.migrate import run_migrations
from memchentic.db.session import _reset_rls_on_checkin, reset_engine, set_rls_context
from memchentic.embeddings.loader import DEFAULT_MODEL_DIR
from memchentic.events import memory_bus
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


def _onnx_files_present() -> bool:
    """True when the MiniLM model and tokenizer files exist."""
    return (DEFAULT_MODEL_DIR / "model.onnx").is_file() and (
        DEFAULT_MODEL_DIR / "tokenizer.json"
    ).is_file()


def pytest_terminal_summary(terminalreporter: Any) -> None:
    """Say so when the suite ran on hash embeddings (shown with ``-q`` too).

    Args:
        terminalreporter: pytest's terminal reporter.
    """
    if not _onnx_files_present():
        terminalreporter.write_line(
            "memchentic: MiniLM files missing; the suite ran on hash embeddings "
            "and the ONNX tests in test_search.py were skipped",
            yellow=True,
        )


def pytest_configure(config: pytest.Config) -> None:
    """Point ``POSTGRES_DB`` at the isolated test database before imports run.

    Args:
        config: Pytest config (unused; hook exists for env side effects).
    """
    del config
    # Production cost (12) takes ~250 ms per hash or verify and dominates suite time.
    passwords.BCRYPT_ROUNDS = 4
    if os.environ.get("MEMCHENTIC_TEST_URL"):
        raise RuntimeError(
            "MEMCHENTIC_TEST_URL is no longer supported. HTTP tests use an "
            "in-process app against memchentic_test. Unset MEMCHENTIC_TEST_URL."
        )
    override = os.environ.get("MEMCHENTIC_TEST_DATABASE_URL", "").strip()
    if override:
        _apply_postgres_url(override)
    else:
        os.environ["POSTGRES_DB"] = _TEST_DB_NAME
    # Encrypt-at-rest migrations / signing keys need a stable non-weak key.
    if len(os.environ.get("ENCRYPTION_KEY", "")) < 32:
        os.environ["ENCRYPTION_KEY"] = "pytest-memchentic-encryption-key-01"
    # MEMCHENTIC_TEST_REQUIRE_ONNX=1 (set in CI) forbids the hash fallback, so
    # ranking and near-duplicate tests always run on real MiniLM vectors.
    if os.environ.get("MEMCHENTIC_TEST_REQUIRE_ONNX") == "1":
        if not _onnx_files_present():
            raise pytest.UsageError(
                "MEMCHENTIC_TEST_REQUIRE_ONNX=1 but model.onnx / tokenizer.json are missing "
                f"under {DEFAULT_MODEL_DIR}. Run scripts/fetch_minilm_onnx.sh on the host."
            )
        os.environ["ALLOW_HASH_EMBEDDINGS"] = "false"
    else:
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
    support.restore_shared_rows()


_DB_FIXTURES = frozenset({"client", "session", "pooled_session", "app_session"})


@pytest.fixture(autouse=True)
def _reset_shared_state(request: pytest.FixtureRequest) -> Iterator[None]:
    """Give every test the same starting state and undo what it leaked.

    Before the test: empty rate-limit buckets. After the test: settings and
    provider caches cleared, leaked event subscribers removed, and (for tests
    that touched the database) the instance-wide rows restored.
    """
    reset_rate_limits()
    subscribers_before = set(memory_bus._subscribers)
    yield
    get_settings.cache_clear()
    clear_discovery_cache()
    clear_jwks_cache()
    reset_rate_limits()
    memory_bus._subscribers.intersection_update(subscribers_before)
    if _DB_FIXTURES.intersection(request.fixturenames):
        support.restore_shared_rows()


@pytest.fixture(scope="session")
def _app_client() -> Iterator[TestClient]:
    """One in-process app for the whole pytest session (lifespan started once).

    Yields:
        A ``TestClient`` with lifespan started (migrations + MCP).
    """
    _assert_isolated(get_settings().database_url)
    reset_engine()
    get_settings.cache_clear()
    # Import here: memchentic.main builds an app at import time, which must
    # happen after pytest_configure has set the test environment.
    from memchentic.main import create_app

    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as test_client:
        yield test_client


@pytest.fixture
def client(_app_client: TestClient) -> Iterator[TestClient]:
    """The shared in-process app with an empty cookie jar for each test.

    Yields:
        The session-wide ``TestClient``.
    """
    _app_client.cookies.clear()
    yield _app_client
    _app_client.cookies.clear()


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Admin-scoped session against the isolated test database.

    This engine has no pool check-in listener, so the RLS context survives
    commits. That is not how the application behaves. Use ``pooled_session``
    for any test of code that commits and then reads or writes again.

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


@pytest_asyncio.fixture
async def pooled_session() -> AsyncIterator[AsyncSession]:
    """Session that loses its RLS context on commit, like the application's.

    The engine registers the same pool check-in listener as the runtime engine
    (``memchentic.db.session.get_engine``). No context is bound; the test binds
    the one it needs.

    Yields:
        An ``AsyncSession`` with no RLS context.
    """
    settings = get_settings()
    _assert_isolated(settings.database_url)
    engine = create_async_engine(settings.database_url, echo=False)
    event.listen(engine.sync_engine, "checkin", _reset_rls_on_checkin)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


@pytest_asyncio.fixture
async def app_session(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncSession]:
    """Session from the real factory (``db.session.get_session_factory()``).

    The engine is built by ``get_engine()``, so it connects as the app role
    and has the pool check-in reset: every commit clears ``app.user_id`` and
    ``app.is_admin`` exactly as in production. No RLS context is set here;
    the test calls ``set_rls_context`` itself.

    A fresh engine is created for the test because asyncpg connections belong
    to one event loop and the application's engine lives in the ``TestClient``
    thread. For the same reason this fixture cannot be combined with
    ``client``.

    Yields:
        An ``AsyncSession`` with no RLS context.
    """
    if "client" in request.fixturenames:
        raise pytest.UsageError("app_session cannot be used together with client")
    _assert_isolated(get_settings().database_url)
    monkeypatch.setattr(db_session_module, "_engine", None)
    monkeypatch.setattr(db_session_module, "_session_factory", None)
    factory = db_session_module.get_session_factory()
    engine = db_session_module.get_engine()
    try:
        async with factory() as db:
            yield db
    finally:
        await engine.dispose()


McpTool = Callable[[dict[str, str], str, dict[str, Any]], dict[str, Any]]


@pytest.fixture
def memory_events_tap() -> Iterator[support.MemoryEventsTap]:
    """Subscribe a thread-safe tap to the memory event bus for one test.

    Yields:
        The tap; call ``tap.wait_for(memory_id)`` to block for an event.
    """
    tap = support.MemoryEventsTap()
    memory_bus._subscribers.add(tap)  # MemoryEventsTap lists the methods the bus calls
    try:
        yield tap
    finally:
        memory_bus._subscribers.discard(tap)


@pytest.fixture
def make_user(client: TestClient) -> Callable[..., support.RegisteredUser]:
    """Return a factory that registers and logs in a fresh internal user."""

    def _make(prefix: str = "t") -> support.RegisteredUser:
        return support.register_user(client, prefix=prefix)

    return _make


@pytest.fixture
def mcp_tool(client: TestClient) -> McpTool:
    """Return ``call(headers, name, arguments)`` giving the JSON-RPC ``result``."""

    def _call(headers: dict[str, str], name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return support.call_tool(client, headers, name, arguments)

    return _call


@pytest.fixture
def admin_headers() -> dict[str, str]:
    """Authorization header for the env admin key (``ADMIN_API_KEY``)."""
    return {"Authorization": f"Bearer {get_settings().admin_api_key}"}


@pytest.fixture
def pkce_pair() -> tuple[str, str]:
    """Return a PKCE ``(verifier, S256 challenge)`` pair."""
    return support.pkce_pair()
