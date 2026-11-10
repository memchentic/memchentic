"""IdP JWT verification hardening: reject alg confusion, missing claims, kid issues."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.exceptions import InvalidTokenError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth import providers
from memchentic.auth.principal import resolve_bearer
from memchentic.auth.providers import clear_jwks_cache, fetch_jwks_and_decode
from memchentic.settings import get_settings

ISSUER = "https://idp.example.com"
AUDIENCE = "https://api.example.com"
KID = "test-kid"


def _b64url_uint(val: int) -> str:
    length = (val.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(val.to_bytes(length, "big")).rstrip(b"=").decode()


@pytest.fixture
def rsa_material() -> tuple[Any, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private_key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": KID,
        "n": _b64url_uint(numbers.n),
        "e": _b64url_uint(numbers.e),
    }
    return private_key, jwk


@pytest.fixture
def idp_settings(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("OAUTH_PROVIDER", "generic")
    monkeypatch.setenv("OAUTH_ISSUER", ISSUER)
    monkeypatch.setenv("OAUTH_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("BASE_URL", "http://memchentic.test")
    get_settings.cache_clear()
    clear_jwks_cache()
    yield get_settings()
    get_settings.cache_clear()
    clear_jwks_cache()


def _base_claims(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-1",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
    }
    claims.update(overrides)
    return claims


def _seed_jwks(monkeypatch: pytest.MonkeyPatch, jwk: dict[str, Any]) -> None:
    async def _fake_fetch(_uri: str) -> dict[str, Any]:
        return {"keys": [jwk]}

    monkeypatch.setattr(
        "memchentic.auth.providers._fetch_jwks",
        _fake_fetch,
    )


@pytest.mark.asyncio
async def test_alg_none_rejected(
    monkeypatch: pytest.MonkeyPatch,
    rsa_material: tuple[Any, dict[str, Any]],
    idp_settings: Any,
) -> None:
    _, jwk = rsa_material
    _seed_jwks(monkeypatch, jwk)
    claims = _base_claims()
    # Unsigned "none" token with matching kid in header.
    token = jwt.encode(claims, key=None, algorithm="none", headers={"kid": KID})
    with pytest.raises((InvalidTokenError, ValueError)):
        await fetch_jwks_and_decode(token, idp_settings)


@pytest.mark.asyncio
async def test_hs256_with_rsa_jwk_rejected(
    monkeypatch: pytest.MonkeyPatch,
    rsa_material: tuple[Any, dict[str, Any]],
    idp_settings: Any,
) -> None:
    _, jwk = rsa_material
    _seed_jwks(monkeypatch, jwk)
    claims = _base_claims()
    token = jwt.encode(
        claims,
        key="hmac-secret-not-rsa-key-long-enough",
        algorithm="HS256",
        headers={"kid": KID},
    )
    with pytest.raises(InvalidTokenError):
        await fetch_jwks_and_decode(token, idp_settings)


@pytest.mark.asyncio
async def test_missing_exp_rejected(
    monkeypatch: pytest.MonkeyPatch,
    rsa_material: tuple[Any, dict[str, Any]],
    idp_settings: Any,
) -> None:
    private_key, jwk = rsa_material
    _seed_jwks(monkeypatch, jwk)
    claims = _base_claims()
    del claims["exp"]
    token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": KID})
    with pytest.raises(InvalidTokenError):
        await fetch_jwks_and_decode(token, idp_settings)


@pytest.mark.asyncio
async def test_missing_aud_rejected(
    monkeypatch: pytest.MonkeyPatch,
    rsa_material: tuple[Any, dict[str, Any]],
    idp_settings: Any,
) -> None:
    private_key, jwk = rsa_material
    _seed_jwks(monkeypatch, jwk)
    claims = _base_claims()
    del claims["aud"]
    token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": KID})
    with pytest.raises(InvalidTokenError):
        await fetch_jwks_and_decode(token, idp_settings)


@pytest.mark.asyncio
async def test_kid_mismatch_rejected(
    monkeypatch: pytest.MonkeyPatch,
    rsa_material: tuple[Any, dict[str, Any]],
    idp_settings: Any,
) -> None:
    private_key, jwk = rsa_material
    _seed_jwks(monkeypatch, jwk)
    claims = _base_claims()
    token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "other-kid"})
    with pytest.raises(ValueError, match="No matching JWKS key"):
        await fetch_jwks_and_decode(token, idp_settings)


@pytest.mark.asyncio
async def test_missing_kid_rejected(
    monkeypatch: pytest.MonkeyPatch,
    rsa_material: tuple[Any, dict[str, Any]],
    idp_settings: Any,
) -> None:
    private_key, jwk = rsa_material
    _seed_jwks(monkeypatch, jwk)
    claims = _base_claims()
    token = jwt.encode(claims, private_key, algorithm="RS256")
    with pytest.raises(ValueError, match="missing kid"):
        await fetch_jwks_and_decode(token, idp_settings)


@pytest.mark.asyncio
async def test_valid_rs256_accepted(
    monkeypatch: pytest.MonkeyPatch,
    rsa_material: tuple[Any, dict[str, Any]],
    idp_settings: Any,
) -> None:
    private_key, jwk = rsa_material
    _seed_jwks(monkeypatch, jwk)
    claims = _base_claims()
    token = jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": KID})
    decoded = await fetch_jwks_and_decode(token, idp_settings)
    assert decoded["sub"] == "user-1"
    assert decoded["aud"] == AUDIENCE


@pytest.mark.asyncio
async def test_jwks_outage_is_not_401(
    monkeypatch: pytest.MonkeyPatch,
    rsa_material: tuple[Any, dict[str, Any]],
    idp_settings: Any,
    session: AsyncSession,
) -> None:
    """A JWKS transport failure propagates; it is not reported as an invalid token."""
    private_key, _ = rsa_material
    token = jwt.encode(_base_claims(), private_key, algorithm="RS256", headers={"kid": KID})

    async def _down(_uri: str) -> dict[str, Any]:
        raise httpx.ConnectError("idp unreachable")

    monkeypatch.setattr("memchentic.auth.providers._fetch_jwks", _down)
    with pytest.raises(httpx.ConnectError):
        await resolve_bearer(session, token, idp_settings)


@pytest.mark.asyncio
async def test_non_jwt_bearer_is_rejected_without_jwks_fetch(
    monkeypatch: pytest.MonkeyPatch,
    idp_settings: Any,
    session: AsyncSession,
) -> None:
    """A bearer value that is not a JWT is unauthenticated and triggers no IdP request."""
    calls: list[str] = []

    async def _record(uri: str) -> dict[str, Any]:
        calls.append(uri)
        return {"keys": []}

    monkeypatch.setattr("memchentic.auth.providers._fetch_jwks", _record)
    assert await resolve_bearer(session, "not-a-jwt", idp_settings) is None
    assert calls == []


@pytest.mark.asyncio
async def test_fetch_jwks_reports_bad_body_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-JSON JWKS response is an IdP outage, not a ValueError (bad token)."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>maintenance</html>")

    real_client = httpx.AsyncClient

    def _client(**kwargs: Any) -> httpx.AsyncClient:
        return real_client(transport=httpx.MockTransport(_handler), **kwargs)

    monkeypatch.setattr(providers.httpx, "AsyncClient", _client)
    clear_jwks_cache()
    with pytest.raises(providers.JwksUnavailableError):
        await providers._fetch_jwks("https://idp.example.com/jwks")
    clear_jwks_cache()


@pytest.mark.asyncio
async def test_kid_with_nul_is_a_bad_token_not_a_database_error(
    monkeypatch: pytest.MonkeyPatch,
    rsa_material: tuple[Any, dict[str, Any]],
    idp_settings: Any,
    session: AsyncSession,
) -> None:
    """A hostile kid never reaches the signing-key query."""
    private_key, jwk = rsa_material
    _seed_jwks(monkeypatch, jwk)
    token = jwt.encode(_base_claims(), private_key, algorithm="RS256", headers={"kid": "a\x00b"})
    assert await resolve_bearer(session, token, idp_settings) is None
    # The session is still usable: no statement failed inside its transaction.
    assert (await session.execute(text("SELECT 1"))).scalar_one() == 1
