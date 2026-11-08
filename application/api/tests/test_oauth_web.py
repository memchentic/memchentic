"""External OAuth: dual JWT verify, web start/callback, first-login upsert."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import resolve_bearer
from memchentic.auth.providers import (
    OAuthProfile,
    clear_discovery_cache,
    clear_jwks_cache,
)
from memchentic.auth.tokens import issue_access_token, verify_access_token
from memchentic.settings import get_settings


@pytest.fixture
def external_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure a google-shaped external provider with a web client id."""
    monkeypatch.setenv("OAUTH_PROVIDER", "google")
    monkeypatch.setenv("OAUTH_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("OAUTH_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("OAUTH_ISSUER", "https://accounts.google.com")
    monkeypatch.setenv("BASE_URL", "http://memchentic.test")
    get_settings.cache_clear()
    clear_discovery_cache()
    clear_jwks_cache()
    yield
    get_settings.cache_clear()
    clear_discovery_cache()
    clear_jwks_cache()


@pytest.mark.asyncio
async def test_session_jwt_verifies_under_external_provider(
    session: AsyncSession, external_oauth: None
) -> None:
    settings = get_settings()
    assert settings.oauth_provider == "google"
    subject = str(uuid.uuid4())
    token = await issue_access_token(
        session,
        subject=subject,
        email="gui@example.com",
        provider="google",
        settings=settings,
    )
    claims = await verify_access_token(session, token, settings)
    assert claims["sub"] == subject
    assert claims["iss"] == settings.session_issuer
    assert claims["email"] == "gui@example.com"


@pytest.mark.asyncio
async def test_idp_jwt_still_accepted_under_external_provider(
    session: AsyncSession, external_oauth: None
) -> None:
    settings = get_settings()
    fake_claims = {
        "sub": f"google-sub-{uuid.uuid4().hex[:8]}",
        "email": f"idp-{uuid.uuid4().hex[:8]}@example.com",
        "email_verified": True,
        "name": "IdP User",
        "iss": settings.oauth_issuer,
        "aud": settings.effective_audience,
    }

    async def fake_decode(token: str, s: object) -> dict:
        del token, s
        return fake_claims

    with patch(
        "memchentic.auth.providers.fetch_jwks_and_decode",
        new=AsyncMock(side_effect=fake_decode),
    ):
        # Unsigned garbage cannot be a session JWT; falls through to IdP path.
        claims = await verify_access_token(session, "not.a.session.jwt", settings)
    assert claims["sub"] == fake_claims["sub"]


def test_oauth_start_rejected_when_internal(client: TestClient) -> None:
    res = client.get("/api/auth/oauth/start", follow_redirects=False)
    assert res.status_code == 400


def test_oauth_start_redirects_to_idp(
    client: TestClient, external_oauth: None
) -> None:
    endpoints = {
        "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_endpoint": "https://oauth2.googleapis.com/token",
        "userinfo_endpoint": "https://openidconnect.googleapis.com/v1/userinfo",
    }
    with patch(
        "memchentic.auth.providers.resolve_oidc_endpoints",
        new=AsyncMock(return_value=endpoints),
    ):
        res = client.get(
            "/api/auth/oauth/start?remember=true", follow_redirects=False
        )
    assert res.status_code == 302
    loc = res.headers["location"]
    assert loc.startswith(endpoints["authorization_endpoint"])
    assert "client_id=test-client-id" in loc
    assert "code_challenge=" in loc
    assert "state=" in loc
    assert "nonce=" in loc
    set_cookie = res.headers.get("set-cookie", "").lower()
    assert "memchentic_oauth" in set_cookie
    assert "memchentic_oauth_nonce" in set_cookie


def test_oauth_callback_sets_session(
    client: TestClient, external_oauth: None
) -> None:
    endpoints = {
        "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_endpoint": "https://oauth2.googleapis.com/token",
        "userinfo_endpoint": "",
    }
    profile = OAuthProfile(
        subject=f"sub-{uuid.uuid4().hex[:10]}",
        email=f"web-{uuid.uuid4().hex[:10]}@example.com",
        email_verified=True,
        display_name="Web User",
    )
    with patch(
        "memchentic.auth.providers.resolve_oidc_endpoints",
        new=AsyncMock(return_value=endpoints),
    ):
        start = client.get("/api/auth/oauth/start", follow_redirects=False)
    assert start.status_code == 302
    state_cookie = start.cookies.get("memchentic_oauth")
    nonce_cookie = start.cookies.get("memchentic_oauth_nonce")
    assert state_cookie
    assert nonce_cookie
    state = state_cookie.split(".", 1)[0]

    with patch(
        "memchentic.auth.oauth_browser.exchange_code_for_profile",
        new=AsyncMock(return_value=profile),
    ):
        cb = client.get(
            f"/api/auth/oauth/callback?code=abc&state={state}",
            cookies={
                "memchentic_oauth": state_cookie,
                "memchentic_oauth_nonce": nonce_cookie,
            },
            follow_redirects=False,
        )
    assert cb.status_code == 302
    assert cb.headers["location"].endswith("/")
    assert "memchentic_session" in cb.cookies
    session_raw = cb.cookies["memchentic_session"]
    refreshed = client.post(
        "/api/auth/refresh",
        headers={
            "X-Memchentic-Client": "gui",
            "Cookie": f"memchentic_session={session_raw}",
        },
    )
    assert refreshed.status_code == 200
    token = refreshed.json()["access_token"]
    me = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == profile.email


def test_oauth_callback_rejects_unverified_email(
    client: TestClient, external_oauth: None
) -> None:
    endpoints = {
        "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_endpoint": "https://oauth2.googleapis.com/token",
        "userinfo_endpoint": "",
    }
    profile = OAuthProfile(
        subject=f"sub-{uuid.uuid4().hex[:10]}",
        email=f"bad-{uuid.uuid4().hex[:10]}@example.com",
        email_verified=False,
        display_name="Nope",
    )
    with patch(
        "memchentic.auth.providers.resolve_oidc_endpoints",
        new=AsyncMock(return_value=endpoints),
    ):
        start = client.get("/api/auth/oauth/start", follow_redirects=False)
    state_cookie = start.cookies["memchentic_oauth"]
    nonce_cookie = start.cookies["memchentic_oauth_nonce"]
    state = state_cookie.split(".", 1)[0]
    with patch(
        "memchentic.auth.oauth_browser.exchange_code_for_profile",
        new=AsyncMock(return_value=profile),
    ):
        cb = client.get(
            f"/api/auth/oauth/callback?code=abc&state={state}",
            cookies={
                "memchentic_oauth": state_cookie,
                "memchentic_oauth_nonce": nonce_cookie,
            },
            follow_redirects=False,
        )
    assert cb.status_code == 302
    assert "sign-in" in cb.headers["location"]
    assert "error=" in cb.headers["location"]


def test_provider_reports_web_oauth(client: TestClient, external_oauth: None) -> None:
    info = client.get("/api/provider").json()
    assert info["web_oauth"] is True
    assert info["oauth_provider"] == "google"
    assert info["oauth_label"] == "Google"


@pytest.mark.asyncio
async def test_resolve_bearer_upserts_external_subject(
    session: AsyncSession, external_oauth: None
) -> None:
    settings = get_settings()
    sub = f"prov-{uuid.uuid4().hex[:12]}"
    email = f"first-{uuid.uuid4().hex[:8]}@example.com"
    fake_claims = {
        "sub": sub,
        "email": email,
        "email_verified": True,
        "name": "First Login",
        "provider": "google",
    }

    # Compact JWT shape; verify_access_token is mocked below.
    bearer = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.sig"

    with patch(
        "memchentic.auth.tokens.verify_access_token",
        new=AsyncMock(return_value=fake_claims),
    ):
        principal = await resolve_bearer(session, bearer, settings)

    assert principal is not None
    assert principal.email == email
    assert principal.user_id is not None

    # Second call finds the durable binding without re-upsert needing claims.
    with patch(
        "memchentic.auth.tokens.verify_access_token",
        new=AsyncMock(return_value={"sub": sub, "provider": "google"}),
    ):
        again = await resolve_bearer(session, bearer, settings)
    assert again is not None
    assert again.user_id == principal.user_id
