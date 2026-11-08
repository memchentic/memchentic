"""Email-as-identity helpers and multi-provider OAuth bindings."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from memchentic.auth.users import (
    ensure_admin_user,
    get_user,
    normalize_email,
    providers_for,
    register_internal_user,
    upsert_oauth_user,
)
from memchentic.models.entities import User
from memchentic.services.spaces import get_personal_space
from memchentic.settings import get_settings


def test_normalize_email_requires_value() -> None:
    """Empty or whitespace-only addresses are rejected."""
    with pytest.raises(ValueError, match="Email is required"):
        normalize_email("  ")
    assert normalize_email("Ada@Example.COM") == "ada@example.com"


@pytest.mark.asyncio
async def test_upsert_oauth_requires_verified_email(session: AsyncSession) -> None:
    """Unverified IdP emails are refused so accounts cannot be claimed blindly."""
    with pytest.raises(ValueError, match="verified"):
        await upsert_oauth_user(
            session,
            provider="google",
            subject="sub-1",
            email="a@example.com",
            email_verified=False,
        )


@pytest.mark.asyncio
async def test_upsert_oauth_meshes_providers_under_email(session: AsyncSession) -> None:
    """Changing OAuth provider keeps the same user and accumulates bindings."""
    suffix = uuid.uuid4().hex[:10]
    email = f"mesh-{suffix}@example.com"
    first = await upsert_oauth_user(
        session,
        provider="google",
        subject=f"google-{suffix}",
        email=email,
        display_name="Mesh",
    )
    second = await upsert_oauth_user(
        session,
        provider="github",
        subject=f"github-{suffix}",
        email=email,
        display_name="Mesh GitHub",
    )
    assert second.id == first.id
    assert second.provider == "github"
    assert second.provider_subject == f"github-{suffix}"
    assert second.email == email
    assert second.display_name == "Mesh GitHub"
    loaded = await get_user(session, first.id)
    assert loaded is not None
    assert providers_for(loaded) == ["github", "google"]


@pytest.mark.asyncio
async def test_internal_then_oauth_keeps_both_bindings(session: AsyncSession) -> None:
    """An internal account gains an OAuth binding when the email matches."""
    suffix = uuid.uuid4().hex[:10]
    email = f"local-{suffix}@example.com"
    local = await register_internal_user(session, email=email, password="password-ok-12")
    linked = await upsert_oauth_user(
        session,
        provider="okta",
        subject=f"okta-{suffix}",
        email=email,
    )
    assert linked.id == local.id
    assert linked.provider == "okta"
    assert linked.provider_subject == f"okta-{suffix}"
    # Password hash is preserved so internal login can still work after link.
    assert linked.password_hash is not None
    result = await session.execute(
        select(User).options(selectinload(User.identities)).where(User.id == local.id)
    )
    loaded = result.scalar_one()
    assert providers_for(loaded) == ["internal", "okta"]


@pytest.mark.asyncio
async def test_ensure_admin_user_is_real_user_with_personal_space(
    session: AsyncSession,
) -> None:
    """Break-glass admin is a durable User row with a personal space."""
    settings = get_settings()
    admin = await ensure_admin_user(session, settings)
    await session.commit()
    again = await ensure_admin_user(session, settings)
    assert again.id == admin.id
    assert admin.email == settings.admin_contact_email.casefold()
    assert await get_personal_space(session, admin.id) is not None
