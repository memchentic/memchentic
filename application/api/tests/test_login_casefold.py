"""Case-insensitive login identifiers (email; admin break-glass is email-only)."""

from __future__ import annotations

import threading
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


async def test_password_hashing_runs_off_the_event_loop(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """bcrypt must not block the event loop thread."""
    import memchentic.auth.users as users_module

    loop_thread = threading.get_ident()
    calls: list[tuple[str, int]] = []
    real_hash = users_module.hash_password
    real_verify = users_module.verify_password

    def spy_hash(password: str) -> str:
        calls.append(("hash", threading.get_ident()))
        return real_hash(password)

    def spy_verify(password: str, password_hash: str) -> bool:
        calls.append(("verify", threading.get_ident()))
        return real_verify(password, password_hash)

    monkeypatch.setattr(users_module, "hash_password", spy_hash)
    monkeypatch.setattr(users_module, "verify_password", spy_verify)

    email = f"thread-{uuid.uuid4().hex[:10]}@example.com"
    await register_internal_user(session, email=email, password="password-ok-12")
    assert await authenticate_internal(session, email=email, password="password-ok-12")
    assert await authenticate_internal(session, email=email, password="wrong") is None
    assert (
        await authenticate_internal(
            session, email=f"missing-{uuid.uuid4().hex}@example.com", password="x"
        )
        is None
    )

    assert [name for name, _ in calls] == ["hash", "verify", "verify", "verify"]
    assert all(thread != loop_thread for _, thread in calls)
