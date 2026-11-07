"""Case-insensitive login identifiers (email and admin username)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from memchentic.auth.principal import check_admin_password
from memchentic.auth.users import authenticate_internal, register_internal_user
from memchentic.db.session import set_rls_context
from memchentic.settings import get_settings


def test_admin_login_identifiers_are_case_insensitive() -> None:
    """Admin username and email match regardless of case; password does not."""
    settings = SimpleNamespace(
        memchentic_admin_user="Admin",
        memchentic_admin_email="Admin@Example.COM",
        memchentic_admin_password="SecretPass",
    )
    assert check_admin_password("admin", "SecretPass", settings)
    assert check_admin_password("ADMIN", "SecretPass", settings)
    assert check_admin_password("admin@example.com", "SecretPass", settings)
    assert check_admin_password("ADMIN@EXAMPLE.COM", "SecretPass", settings)
    assert not check_admin_password("admin", "secretpass", settings)
    assert not check_admin_password("nobody", "SecretPass", settings)


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    """Database session with admin RLS so personal-space inserts succeed.

    Yields:
        An AsyncSession against the configured DATABASE_URL.
    """
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await set_rls_context(session, user_id=None, is_admin=True)
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_internal_login_email_is_case_insensitive(session: AsyncSession) -> None:
    """Registered emails authenticate when the login form uses different casing."""
    suffix = uuid.uuid4().hex[:10]
    email = f"Case-{suffix}@Example.COM"
    created = await register_internal_user(
        session, email=email, password="password-ok-12"
    )
    assert created.email == f"case-{suffix}@example.com"
    user = await authenticate_internal(
        session, email=f"CASE-{suffix}@EXAMPLE.com", password="password-ok-12"
    )
    assert user is not None
    assert user.id == created.id
    assert (
        await authenticate_internal(
            session, email=f"CASE-{suffix}@EXAMPLE.com", password="wrong"
        )
        is None
    )
