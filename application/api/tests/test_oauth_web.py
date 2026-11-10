"""External OAuth: dual JWT verify, web start/callback, first-login upsert."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from jwt.exceptions import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import resolve_bearer
from memchentic.auth.providers import (
    OAuthProfile,
    _pin_discovery_origins,
    build_authorize_url,
    clear_discovery_cache,
    clear_jwks_cache,
    resolve_scopes,
)
from memchentic.auth.tokens import issue_access_token, verify_access_token
from memchentic.auth.users import register_internal_user
from memchentic.models.entities import UserIdentity
from memchentic.settings import Settings, get_settings


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


def test_oauth_start_redirects_to_idp(client: TestClient, external_oauth: None) -> None:
    endpoints = {
        "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_endpoint": "https://oauth2.googleapis.com/token",
        "userinfo_endpoint": "https://openidconnect.googleapis.com/v1/userinfo",
    }
    with patch(
        "memchentic.auth.providers.resolve_oidc_endpoints",
        new=AsyncMock(return_value=endpoints),
    ):
        res = client.get("/api/auth/oauth/start?remember=true", follow_redirects=False)
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


def test_oauth_callback_sets_session(client: TestClient, external_oauth: None) -> None:
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


def test_oauth_callback_rejects_unverified_email(client: TestClient, external_oauth: None) -> None:
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


async def test_idp_jwt_uuid_sub_is_not_a_local_user_id(
    session: AsyncSession, external_oauth: None
) -> None:
    """An IdP token whose sub equals a local user id is not that user."""
    settings = get_settings()
    victim = await register_internal_user(
        session,
        email=f"victim-{uuid.uuid4().hex[:10]}@example.com",
        password="password-ok-12",
    )
    idp_claims = {"iss": settings.oauth_issuer, "sub": str(victim.id)}
    with patch(
        "memchentic.auth.tokens.verify_access_token",
        new=AsyncMock(return_value=idp_claims),
    ):
        principal = await resolve_bearer(session, "idp.jwt.token", settings)
    assert principal is None


async def test_idp_jwt_uuid_sub_provisions_by_identity(
    session: AsyncSession, external_oauth: None
) -> None:
    """A UUID-shaped IdP subject is provisioned through user_identities."""
    settings = get_settings()
    sub = str(uuid.uuid4())
    email = f"kc-{uuid.uuid4().hex[:10]}@example.com"
    idp_claims = {
        "iss": settings.oauth_issuer,
        "sub": sub,
        "email": email,
        "email_verified": True,
        "name": "UUID Subject",
    }
    with patch(
        "memchentic.auth.tokens.verify_access_token",
        new=AsyncMock(return_value=idp_claims),
    ):
        principal = await resolve_bearer(session, "idp.jwt.token", settings)
    assert principal is not None
    assert principal.email == email
    assert str(principal.user_id) != sub
    binding = (
        await session.execute(
            select(UserIdentity).where(
                UserIdentity.provider == "google",
                UserIdentity.provider_subject == sub,
            )
        )
    ).scalar_one()
    assert binding.user_id == principal.user_id


async def test_idp_jwt_provider_claim_cannot_pick_the_binding(
    session: AsyncSession, external_oauth: None
) -> None:
    """A ``provider`` claim in an IdP token cannot select another provider's binding."""
    settings = get_settings()
    victim = await register_internal_user(
        session,
        email=f"internal-{uuid.uuid4().hex[:10]}@example.com",
        password="password-ok-12",
    )
    idp_claims = {
        "iss": settings.oauth_issuer,
        "sub": victim.email,
        "provider": "internal",
    }
    with patch(
        "memchentic.auth.tokens.verify_access_token",
        new=AsyncMock(return_value=idp_claims),
    ):
        principal = await resolve_bearer(session, "idp.jwt.token", settings)
    assert principal is None


async def test_idp_issuer_equal_to_base_url_is_rejected(
    session: AsyncSession, external_oauth: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The IdP verifier is never consulted when its issuer equals BASE_URL."""
    monkeypatch.setenv("OAUTH_ISSUER", "http://memchentic.test")
    get_settings.cache_clear()
    settings = get_settings()
    decode = AsyncMock(return_value={"sub": "x", "iss": "http://memchentic.test"})
    with (
        patch("memchentic.auth.providers.fetch_jwks_and_decode", new=decode),
        pytest.raises(InvalidTokenError),
    ):
        await verify_access_token(session, "not.a.session.jwt", settings)
    decode.assert_not_awaited()


_GOOGLE_DISCOVERY = {
    "issuer": "https://accounts.google.com",
    "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
    "token_endpoint": "https://oauth2.googleapis.com/token",
    "userinfo_endpoint": "https://openidconnect.googleapis.com/v1/userinfo",
    "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
}


def test_google_discovery_document_passes_origin_pin() -> None:
    issuer = "https://accounts.google.com"
    _pin_discovery_origins(issuer, _GOOGLE_DISCOVERY, provider="google")
    with pytest.raises(ValueError, match="origin mismatch"):
        _pin_discovery_origins(issuer, _GOOGLE_DISCOVERY, provider="generic")
    with pytest.raises(ValueError, match="origin mismatch"):
        _pin_discovery_origins(issuer, _GOOGLE_DISCOVERY)
    for bad in (
        "https://evil.example/token",
        "http://oauth2.googleapis.com/token",
        "https://oauth2.googleapis.com:8443/token",
        "https://oauth2.googleapis.com.evil.example/token",
    ):
        with pytest.raises(ValueError, match="token_endpoint origin mismatch"):
            _pin_discovery_origins(
                issuer, {**_GOOGLE_DISCOVERY, "token_endpoint": bad}, provider="google"
            )


def test_entra_userinfo_endpoint_passes_origin_pin() -> None:
    base = "https://login.microsoftonline.com/11111111-2222-3333-4444-555555555555"
    issuer = f"{base}/v2.0"
    doc = {
        "issuer": issuer,
        "authorization_endpoint": f"{base}/oauth2/v2.0/authorize",
        "token_endpoint": f"{base}/oauth2/v2.0/token",
        "jwks_uri": f"{base}/discovery/v2.0/keys",
        "userinfo_endpoint": "https://graph.microsoft.com/oidc/userinfo",
    }
    _pin_discovery_origins(issuer, doc, provider="entra")
    with pytest.raises(ValueError, match="userinfo_endpoint origin mismatch"):
        _pin_discovery_origins(issuer, doc, provider="google")


async def test_google_authorize_url_built_from_discovery(
    external_oauth: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real discovery path (no resolve_oidc_endpoints mock) works for Google."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://accounts.google.com/.well-known/openid-configuration"
        return httpx.Response(200, json=_GOOGLE_DISCOVERY)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "memchentic.auth.providers.httpx.AsyncClient",
        lambda *args, **kwargs: real_client(
            *args, **{**kwargs, "transport": httpx.MockTransport(handler)}
        ),
    )
    url = await build_authorize_url(get_settings(), state="s", code_challenge="c", nonce="n")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert "client_id=test-client-id" in url


async def test_default_scopes_follow_provider_preset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OAUTH_SCOPES", raising=False)
    github = Settings(OAUTH_PROVIDER="github", OAUTH_CLIENT_ID="gh-client", _env_file=None)
    assert resolve_scopes(github) == "read:user,user:email"
    entra = Settings(OAUTH_PROVIDER="entra", _env_file=None)
    assert resolve_scopes(entra) == "openid,profile,email,offline_access"
    google = Settings(OAUTH_PROVIDER="google", _env_file=None)
    assert resolve_scopes(google) == "openid,profile,email"
    internal = Settings(OAUTH_PROVIDER="internal", _env_file=None)
    assert resolve_scopes(internal) == "openid,profile,email"
    explicit = Settings(OAUTH_PROVIDER="github", OAUTH_SCOPES="read:org", _env_file=None)
    assert resolve_scopes(explicit) == "read:org"

    # GitHub endpoints are constants, so this makes no network call.
    url = await build_authorize_url(github, state="s", code_challenge="c")
    assert "scope=read%3Auser+user%3Aemail" in url


def test_first_idp_link_revokes_password_keys_and_sessions(
    client: TestClient, request: pytest.FixtureRequest
) -> None:
    """A squatter's password, API key and session stop working after the owner's IdP sign-in."""
    email = f"squat-{uuid.uuid4().hex[:10]}@example.com"
    password = "password-ok-12"
    reg = client.post("/api/auth/register", json={"email": email, "password": password})
    assert reg.status_code == 200, reg.text
    login = client.post("/api/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    old_cookie = login.cookies["memchentic_session"]
    bearer = {"Authorization": f"Bearer {login.json()['access_token']}"}
    api_key = client.post("/api/api-keys", headers=bearer, json={"name": "squat"}).json()["key"]
    key_auth = {"Authorization": f"Bearer {api_key}"}
    assert client.get("/api/me", headers=key_auth).status_code == 200
    client.cookies.clear()

    # Switch to the external provider only now: registration needs internal mode.
    request.getfixturevalue("external_oauth")
    endpoints = {
        "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_endpoint": "https://oauth2.googleapis.com/token",
        "userinfo_endpoint": "",
    }
    profile = OAuthProfile(
        subject=f"sub-{uuid.uuid4().hex[:10]}",
        email=email,
        email_verified=True,
        display_name="Real Owner",
    )
    with patch(
        "memchentic.auth.providers.resolve_oidc_endpoints",
        new=AsyncMock(return_value=endpoints),
    ):
        start = client.get("/api/auth/oauth/start", follow_redirects=False)
    assert start.status_code == 302
    state_cookie = start.cookies["memchentic_oauth"]
    nonce_cookie = start.cookies["memchentic_oauth_nonce"]
    with patch(
        "memchentic.auth.oauth_browser.exchange_code_for_profile",
        new=AsyncMock(return_value=profile),
    ):
        cb = client.get(
            f"/api/auth/oauth/callback?code=abc&state={state_cookie.split('.', 1)[0]}",
            cookies={
                "memchentic_oauth": state_cookie,
                "memchentic_oauth_nonce": nonce_cookie,
            },
            follow_redirects=False,
        )
    assert cb.status_code == 302
    assert "memchentic_session" in cb.cookies
    client.cookies.clear()

    # The API key is not a JWT; stop the IdP fallback from reaching the network.
    with patch(
        "memchentic.auth.providers.fetch_jwks_and_decode",
        new=AsyncMock(side_effect=ValueError("not an IdP token")),
    ):
        assert client.get("/api/me", headers=key_auth).status_code == 401
    relogin = client.post("/api/auth/login", json={"email": email, "password": password})
    assert relogin.status_code == 401
    stale = client.post(
        "/api/auth/refresh",
        headers={"X-Memchentic-Client": "gui", "Cookie": f"memchentic_session={old_cookie}"},
    )
    assert stale.status_code == 401


def test_dcr_refused_in_external_mode(client: TestClient, external_oauth: None) -> None:
    res = client.post(
        "/oauth/register",
        json={"client_name": "x", "redirect_uris": ["https://app.example.com/cb"]},
    )
    assert res.status_code == 400
    assert "OAUTH_PROVIDER=internal" in res.json()["detail"]


_AS_META_URL = "/.well-known/oauth-authorization-server"


def test_external_as_metadata_relays_discovery(client: TestClient, external_oauth: None) -> None:
    doc = {
        "issuer": "https://accounts.google.com",
        "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_endpoint": "https://oauth2.googleapis.com/token",
        "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
    }
    with patch(
        "memchentic.auth.providers._oidc_discovery",
        new=AsyncMock(return_value=doc),
    ):
        res = client.get(_AS_META_URL)
    assert res.status_code == 200, res.text
    meta = res.json()
    assert meta["issuer"] == doc["issuer"]
    assert meta["authorization_endpoint"] == doc["authorization_endpoint"]
    assert meta["token_endpoint"] == doc["token_endpoint"]
    assert meta["jwks_uri"] == doc["jwks_uri"]
    assert meta["code_challenge_methods_supported"] == ["S256"]
    assert "registration_endpoint" not in meta


def test_external_as_metadata_404_without_discovery(
    client: TestClient, external_oauth: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OAUTH_PROVIDER", "github")
    get_settings.cache_clear()
    assert client.get(_AS_META_URL).status_code == 404


def test_external_as_metadata_503_when_discovery_fails(
    client: TestClient, external_oauth: None
) -> None:
    with patch(
        "memchentic.auth.providers._oidc_discovery",
        new=AsyncMock(side_effect=httpx.ConnectError("down")),
    ):
        res = client.get(_AS_META_URL)
    assert res.status_code == 503


def test_internal_as_metadata_unchanged(client: TestClient) -> None:
    base = get_settings().base_url.rstrip("/")
    meta = client.get(_AS_META_URL).json()
    assert meta["issuer"] == base
    assert meta["authorization_endpoint"] == f"{base}/oauth/authorize"
    assert meta["token_endpoint"] == f"{base}/oauth/token"
    assert meta["jwks_uri"] == f"{base}/oauth/jwks"
    assert meta["registration_endpoint"] == f"{base}/oauth/register"
    assert meta["code_challenge_methods_supported"] == ["S256"]
    assert meta["scopes_supported"] == ["openid", "profile", "email"]
