"""IdP JWT verification hardening: reject alg confusion, missing claims, kid issues."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.exceptions import InvalidTokenError

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
    token = jwt.encode(
        claims, private_key, algorithm="RS256", headers={"kid": "other-kid"}
    )
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
