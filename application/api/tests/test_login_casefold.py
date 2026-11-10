"""Case-insensitive login identifiers (email; admin break-glass is email-only)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import check_admin_password
from memchentic.auth.users import authenticate_internal, register_internal_user


def test_admin_login_email_is_case_insensitive() -> None:
    """Break-glass matches admin email case-insensitively; display name is not a login."""
    settings = SimpleNamespace(
        admin_display_name="Admin",
        admin_email="Admin@Example.COM",
        admin_password="SecretPass",
        admin_contact_email="admin@example.com",
    )
    assert check_admin_password("admin@example.com", "SecretPass", settings)
    assert check_admin_password("ADMIN@EXAMPLE.COM", "SecretPass", settings)
    assert not check_admin_password("admin", "SecretPass", settings)
    assert not check_admin_password("Admin", "SecretPass", settings)
    assert not check_admin_password("admin@example.com", "secretpass", settings)
    assert not check_admin_password("nobody@example.com", "SecretPass", settings)


@pytest.mark.asyncio
async def test_internal_login_email_is_case_insensitive(session: AsyncSession) -> None:
    """Registered emails authenticate when the login form uses different casing."""
    suffix = uuid.uuid4().hex[:10]
    email = f"Case-{suffix}@Example.COM"
    created = await register_internal_user(session, email=email, password="password-ok-12")
    assert created.email == f"case-{suffix}@example.com"
    user = await authenticate_internal(
        session, email=f"CASE-{suffix}@EXAMPLE.com", password="password-ok-12"
    )
    assert user is not None
    assert user.id == created.id
    assert (
        await authenticate_internal(session, email=f"CASE-{suffix}@EXAMPLE.com", password="wrong")
        is None
    )
