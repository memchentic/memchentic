"""OAuth AS hardening: redirect_uri, atomic codes, refresh rotation, CSRF."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import uuid
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

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
