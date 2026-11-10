"""Email-as-identity helpers and multi-provider OAuth bindings."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from memchentic.auth.users import (
    authenticate_internal,
    ensure_admin_user,
    get_user,
    normalize_email,
    providers_for,
    register_internal_user,
    upsert_oauth_user,
)
from memchentic.db.session import set_rls_context
from memchentic.models.entities import ApiKey, AuthRefreshToken, GuiSession, User
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


async def test_internal_then_oauth_link_revokes_unverified_credentials(
    session: AsyncSession,
) -> None:
    """The first IdP link drops the password, sessions, refresh tokens and API keys."""
    suffix = uuid.uuid4().hex[:10]
    email = f"local-{suffix}@example.com"
    local = await register_internal_user(session, email=email, password="password-ok-12")
    now = datetime.now(UTC)
    await set_rls_context(session, user_id=local.id, is_admin=False)
    session.add_all(
        [
            ApiKey(
                id=uuid.uuid4(),
                user_id=local.id,
                name="squat",
                key_prefix="mc_test",
                key_hash=uuid.uuid4().hex,
            ),
            GuiSession(
                id=uuid.uuid4(),
                token_hash=uuid.uuid4().hex,
                subject=str(local.id),
                email=email,
                provider="internal",
                user_id=local.id,
                persistent=False,
                expires_at=now + timedelta(hours=1),
            ),
            AuthRefreshToken(
                id=uuid.uuid4(),
                token_hash=uuid.uuid4().hex,
                family_id=uuid.uuid4(),
                client_id="mcp_test",
                user_id=local.id,
                scope="openid",
                expires_at=now + timedelta(days=1),
            ),
        ]
    )
    await session.commit()

    linked = await upsert_oauth_user(
        session,
        provider="okta",
        subject=f"okta-{suffix}",
        email=email,
    )
    assert linked.id == local.id
    assert linked.provider == "okta"
    assert linked.provider_subject == f"okta-{suffix}"
    assert linked.password_hash is None
    assert await authenticate_internal(session, email=email, password="password-ok-12") is None

    await set_rls_context(session, user_id=None, is_admin=True)
    result = await session.execute(
        select(User).options(selectinload(User.identities)).where(User.id == local.id)
    )
    loaded = result.scalar_one()
    assert providers_for(loaded) == ["okta"]
    for model in (ApiKey, GuiSession, AuthRefreshToken):
        live = await session.scalar(
            select(func.count())
            .select_from(model)
            .where(model.user_id == local.id, model.revoked_at.is_(None))
        )
        assert live == 0, model.__name__


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
