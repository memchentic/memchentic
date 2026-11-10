"""OAuth AS hardening: redirect_uri, atomic codes, refresh rotation, CSRF."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.api.oauth import prune_stale_oauth_rows
from memchentic.auth.tokens import ACCESS_TOKEN_SECONDS
from memchentic.auth.users import register_internal_user
from memchentic.models.entities import AuthRefreshToken, OAuthClient
from memchentic.settings import get_settings

_CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _csrf_from_page(html: str) -> str:
    match = _CSRF_RE.search(html)
    assert match, "csrf_token hidden field missing from authorize page"
    return match.group(1)


def _register_user(client: TestClient) -> tuple[str, str]:
    email = f"oas-{uuid.uuid4().hex[:10]}@example.com"
    password = "pw-" + uuid.uuid4().hex
    res = client.post("/api/auth/register", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    return email, password


def _register_client(client: TestClient, redirect: str) -> str:
    res = client.post(
        "/oauth/register",
        json={"client_name": "Sec Harness", "redirect_uris": [redirect]},
    )
    assert res.status_code == 200, res.text
    return res.json()["client_id"]


def _authorize_code(
    client: TestClient,
    *,
    email: str,
    password: str,
    client_id: str,
    redirect: str,
    challenge: str,
) -> str:
    page = client.get(
        "/oauth/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    assert page.status_code == 200, page.text
    csrf = _csrf_from_page(page.text)
    redirected = client.post(
        "/oauth/authorize",
        data={
            "email": email,
            "password": password,
            "client_id": client_id,
            "redirect_uri": redirect,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "csrf_token": csrf,
        },
        follow_redirects=False,
    )
    assert redirected.status_code == 302, redirected.text
    return parse_qs(urlparse(redirected.headers["location"]).query)["code"][0]


def test_hostile_redirect_uri_rejected(client: TestClient) -> None:
    for uri in (
        "javascript:alert(1)",
        "data:text/html,hi",
        "file:///etc/passwd",
        "",
        "/relative/cb",
        "http://evil.example/cb",
        "https://ok.example/cb#frag",
        "https://user:pass@ok.example/cb",
    ):
        res = client.post(
            "/oauth/register",
            json={"client_name": "bad", "redirect_uris": [uri]},
        )
        assert res.status_code == 400, uri


def test_loopback_http_and_https_accepted(client: TestClient) -> None:
    for uri in (
        "http://127.0.0.1:9/cb",
        "http://localhost/cb",
        "http://[::1]:8080/cb",
        "https://app.example.com/oauth/callback",
    ):
        res = client.post(
            "/oauth/register",
            json={"client_name": "ok", "redirect_uris": [uri]},
        )
        assert res.status_code == 200, (uri, res.text)
        assert uri in res.json()["redirect_uris"]


def test_double_code_redemption_fails(client: TestClient) -> None:
    email, password = _register_user(client)
    redirect = "http://127.0.0.1:9/cb"
    client_id = _register_client(client, redirect)
    verifier, challenge = _pkce_pair()
    code = _authorize_code(
        client,
        email=email,
        password=password,
        client_id=client_id,
        redirect=redirect,
        challenge=challenge,
    )
    grant = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "redirect_uri": redirect,
        "code_verifier": verifier,
    }
    first = client.post("/oauth/token", data=grant)
    assert first.status_code == 200, first.text
    second = client.post("/oauth/token", data=grant)
    assert second.status_code == 400


def test_refresh_rotation_and_reuse_detection(client: TestClient) -> None:
    email, password = _register_user(client)
    redirect = "http://127.0.0.1:9/cb"
    client_id = _register_client(client, redirect)
    verifier, challenge = _pkce_pair()
    code = _authorize_code(
        client,
        email=email,
        password=password,
        client_id=client_id,
        redirect=redirect,
        challenge=challenge,
    )
    tokens = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": redirect,
            "code_verifier": verifier,
        },
    )
    assert tokens.status_code == 200, tokens.text
    body = tokens.json()
    old_refresh = body["refresh_token"]
    assert old_refresh

    rotated = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": old_refresh,
            "client_id": client_id,
        },
    )
    assert rotated.status_code == 200, rotated.text
    new_refresh = rotated.json()["refresh_token"]
    assert new_refresh
    assert new_refresh != old_refresh

    reuse = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": old_refresh,
            "client_id": client_id,
        },
    )
    assert reuse.status_code == 400

    # Family invalidated: the rotated token must also fail after reuse detection.
    after = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": new_refresh,
            "client_id": client_id,
        },
    )
    assert after.status_code == 400


def test_authorize_post_without_csrf_fails(client: TestClient) -> None:
    email, password = _register_user(client)
    redirect = "http://127.0.0.1:9/cb"
    client_id = _register_client(client, redirect)
    _, challenge = _pkce_pair()
    page = client.get(
        "/oauth/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    assert page.status_code == 200
    # Cookie is present but form omits csrf_token.
    res = client.post(
        "/oauth/authorize",
        data={
            "email": email,
            "password": password,
            "client_id": client_id,
            "redirect_uri": redirect,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    assert res.status_code == 400


def test_dcr_rejects_malformed_metadata(client: TestClient) -> None:
    ok_uri = "https://app.example.com/cb"
    for body in (
        {"redirect_uris": 5},
        {"redirect_uris": {"a": 1}},
        {"redirect_uris": [5]},
        {"redirect_uris": []},
        {"redirect_uris": [ok_uri], "client_name": {"a": 1}},
        {"redirect_uris": [f"{ok_uri}/{i}" for i in range(11)]},
        {"redirect_uris": ["https://app.example.com/" + "a" * 1100]},
        {"redirect_uris": ["https://app.example.com/a b"]},
    ):
        res = client.post("/oauth/register", json=body)
        assert res.status_code == 400, (body, res.text)


def test_dcr_truncates_long_client_name(client: TestClient) -> None:
    res = client.post(
        "/oauth/register",
        json={"redirect_uris": ["https://app.example.com/cb"], "client_name": "n" * 300},
    )
    assert res.status_code == 200, res.text
    assert res.json()["client_name"] == "n" * 255


def test_dcr_is_rate_limited(client: TestClient) -> None:
    body = {"redirect_uris": ["https://app.example.com/cb"]}
    codes = [client.post("/oauth/register", json=body).status_code for _ in range(11)]
    assert codes[:10] == [200] * 10
    assert codes[10] == 429


def test_authorize_rejects_oversized_scope(client: TestClient) -> None:
    redirect = "http://127.0.0.1:9/cb"
    client_id = _register_client(client, redirect)
    _, challenge = _pkce_pair()
    res = client.get(
        "/oauth/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "x" * 600,
        },
    )
    assert res.status_code == 422


async def test_stale_unused_clients_are_pruned(session: AsyncSession) -> None:
    old = datetime.now(UTC) - timedelta(days=2)
    stale_id, fresh_id, used_id = (f"mcp_{tag}_{uuid.uuid4().hex[:8]}" for tag in "sfu")
    owner = await register_internal_user(
        session,
        email=f"prune-{uuid.uuid4().hex[:10]}@example.com",
        password="password-ok-12",
    )
    uris = ["https://app.example.com/cb"]
    session.add_all(
        [
            OAuthClient(
                id=uuid.uuid4(), client_id=stale_id, redirect_uris=uris, name="s", created_at=old
            ),
            OAuthClient(id=uuid.uuid4(), client_id=fresh_id, redirect_uris=uris, name="f"),
            OAuthClient(
                id=uuid.uuid4(), client_id=used_id, redirect_uris=uris, name="u", created_at=old
            ),
            AuthRefreshToken(
                id=uuid.uuid4(),
                token_hash=uuid.uuid4().hex,
                family_id=uuid.uuid4(),
                client_id=used_id,
                user_id=owner.id,
                scope="openid",
                expires_at=datetime.now(UTC) + timedelta(days=30),
            ),
        ]
    )
    await session.commit()

    await prune_stale_oauth_rows(session, force=True)
    await session.commit()

    remaining = set(
        (
            await session.execute(
                select(OAuthClient.client_id).where(
                    OAuthClient.client_id.in_([stale_id, fresh_id, used_id])
                )
            )
        ).scalars()
    )
    assert remaining == {fresh_id, used_id}


def test_native_redirect_scheme_accepted_when_allowlisted(client: TestClient) -> None:
    """A cursor:// redirect registers and completes the code flow with PKCE."""
    email, password = _register_user(client)
    redirect = "cursor://anysphere.cursor-mcp/oauth/callback"
    client_id = _register_client(client, redirect)
    verifier, challenge = _pkce_pair()
    code = _authorize_code(
        client,
        email=email,
        password=password,
        client_id=client_id,
        redirect=redirect,
        challenge=challenge,
    )
    tokens = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": redirect,
            "code_verifier": verifier,
        },
    )
    assert tokens.status_code == 200, tokens.text


def test_unlisted_private_scheme_rejected(client: TestClient) -> None:
    for uri in (
        "evil://x/cb",
        "javascript://x/cb",
        "cursor://x/cb#frag",
        "cursor://user:pw@x/cb",
        "cursor:",
    ):
        res = client.post(
            "/oauth/register",
            json={"client_name": "bad", "redirect_uris": [uri]},
        )
        assert res.status_code == 400, uri


def test_mcp_401_has_cors_headers(client: TestClient) -> None:
    origins = get_settings().cors_origin_list
    if not origins:
        pytest.skip("CORS_ORIGINS is empty")
    origin = origins[0]

    res = client.post("/mcp", headers={"Origin": origin, "Accept": "application/json"}, json={})
    assert res.status_code == 401
    assert res.headers.get("access-control-allow-origin") == origin
    assert "www-authenticate" in res.headers
    exposed = res.headers.get("access-control-expose-headers", "").lower()
    assert "www-authenticate" in exposed

    preflight = client.options(
        "/mcp",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers.get("access-control-allow-origin") == origin


def test_token_errors_are_rfc6749_shaped(client: TestClient) -> None:
    email, password = _register_user(client)
    redirect = "http://127.0.0.1:9/cb"
    client_id = _register_client(client, redirect)
    verifier, challenge = _pkce_pair()

    missing = client.post("/oauth/token", data={"client_id": client_id})
    assert missing.status_code == 400
    assert missing.json()["error"] == "invalid_request"

    unsupported = client.post(
        "/oauth/token", data={"grant_type": "password", "client_id": client_id}
    )
    assert unsupported.status_code == 400
    assert unsupported.json()["error"] == "unsupported_grant_type"
    assert unsupported.headers["cache-control"] == "no-store"

    bad_code = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": "not-a-code",
            "client_id": client_id,
            "redirect_uri": redirect,
            "code_verifier": verifier,
        },
    )
    assert bad_code.status_code == 400
    body = bad_code.json()
    assert body["error"] == "invalid_grant"
    assert body["error_description"]
    assert "detail" not in body

    code = _authorize_code(
        client,
        email=email,
        password=password,
        client_id=client_id,
        redirect=redirect,
        challenge=challenge,
    )
    ok = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": redirect,
            "code_verifier": verifier,
        },
    )
    assert ok.status_code == 200, ok.text
    assert ok.headers["cache-control"] == "no-store"
    assert ok.headers["pragma"] == "no-cache"
    assert ok.json()["expires_in"] == ACCESS_TOKEN_SECONDS

    bad_refresh = client.post(
        "/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": "nope", "client_id": client_id},
    )
    assert bad_refresh.status_code == 400
    assert bad_refresh.json()["error"] == "invalid_grant"


def _cookie_attributes(set_cookie: str) -> list[str]:
    return [part.strip().lower() for part in set_cookie.split(";")]


def test_csrf_cookie_secure_follows_https_base_url(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    redirect = "http://127.0.0.1:9/cb"
    client_id = _register_client(client, redirect)
    _, challenge = _pkce_pair()
    params = {
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    try:
        plain = client.get("/oauth/authorize", params=params)
        assert plain.status_code == 200, plain.text
        assert "secure" not in _cookie_attributes(plain.headers["set-cookie"])

        # Public URL is https, socket is still http://testserver (TLS at a proxy).
        monkeypatch.setenv("BASE_URL", "https://memchentic.example")
        get_settings.cache_clear()
        proxied = client.get("/oauth/authorize", params=params)
        assert proxied.status_code == 200, proxied.text
        assert "secure" in _cookie_attributes(proxied.headers["set-cookie"])
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
        client.cookies.clear()


def test_non_ascii_csrf_is_400(client: TestClient) -> None:
    client_id = _register_client(client, "https://example.com/cb")
    client.cookies.clear()
    body = (
        f"client_id={client_id}&redirect_uri=https%3A%2F%2Fexample.com%2Fcb"
        f"&code_challenge={'a' * 43}&code_challenge_method=S256"
        "&email=a%40b.c&password=x&csrf_token=%C3%A9"
    ).encode()
    res = client.post(
        "/oauth/authorize",
        content=body,
        headers=[
            (b"content-type", b"application/x-www-form-urlencoded"),
            (b"cookie", "memchentic_oauth_csrf=é".encode()),
        ],
        follow_redirects=False,
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "Invalid CSRF token"


def test_as_metadata_advertises_only_public_clients(client: TestClient) -> None:
    """The internal AS supports public clients only, so only ``none`` is advertised."""
    meta = client.get("/.well-known/oauth-authorization-server").json()
    assert meta["token_endpoint_auth_methods_supported"] == ["none"]
