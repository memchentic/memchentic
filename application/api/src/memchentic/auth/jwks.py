"""JWKS fetch, cache and IdP JWT verification."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

from memchentic.auth.providers import discover_jwks_uri, resolve_issuer
from memchentic.settings import Settings

_JWKS_TTL_SECONDS = 300


_jwks_cache: dict[str, tuple[float, dict[str, Any]]] = {}


class JwksUnavailableError(RuntimeError):
    """The IdP JWKS document could not be fetched or parsed (not a token problem)."""


async def _fetch_jwks(jwks_uri: str) -> dict[str, Any]:
    now = time.monotonic()
    cached = _jwks_cache.get(jwks_uri)
    if cached and cached[0] > now:
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(jwks_uri)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        # Transport failure, non-2xx status or a non-JSON body: an outage on
        # the IdP side, never a reason to call the caller's token invalid.
        raise JwksUnavailableError(f"JWKS fetch failed: {type(exc).__name__}") from exc
    if not isinstance(data, dict):
        raise JwksUnavailableError("JWKS document is not a JSON object")
    _jwks_cache[jwks_uri] = (now + _JWKS_TTL_SECONDS, data)
    return data


async def fetch_jwks_and_decode(
    token: str, settings: Settings, *, audience: str | None = None
) -> dict:
    """Verify an IdP JWT against JWKS with pinned algorithms and required claims.

    Args:
        token: Compact JWT from the IdP.
        settings: App settings (audience, issuer, JWKS URI).
        audience: Expected ``aud``. Defaults to the API audience
            (``settings.effective_audience``), which is right for Bearer access
            tokens. Pass the OAuth client id when verifying an OIDC id_token.

    Returns:
        Decoded and verified claims.

    Raises:
        ValueError: When audience/issuer/kid/key type is invalid.
        jwt.exceptions.InvalidTokenError: When signature or claims fail.
    """
    expected_audience = audience or settings.effective_audience
    if not expected_audience:
        raise ValueError("Audience is required for external JWT verification")
    issuer = resolve_issuer(settings)
    if not issuer:
        raise ValueError("Issuer is required for external JWT verification")

    # Parse the header before any network call: a bearer value that is not a
    # JWT, or has no kid, is rejected without contacting the IdP.
    header = jwt.get_unverified_header(token)
    kid = header.get("kid")
    if not kid:
        raise ValueError("JWT header missing kid")

    jwks = await _fetch_jwks(await discover_jwks_uri(settings))

    key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if key is None:
        raise ValueError("No matching JWKS key")

    kty = key.get("kty")
    crypto_key: RSAPublicKey | EllipticCurvePublicKey
    if kty == "RSA":
        rsa_key = RSAAlgorithm.from_jwk(json.dumps(key))
        if not isinstance(rsa_key, RSAPublicKey):
            raise ValueError("JWKS RSA key is not a public key")
        crypto_key = rsa_key
    elif kty == "EC":
        ec_key = ECAlgorithm.from_jwk(json.dumps(key))
        if not isinstance(ec_key, EllipticCurvePublicKey):
            raise ValueError("JWKS EC key is not a public key")
        crypto_key = ec_key
    else:
        raise ValueError(f"Unsupported JWKS kty: {kty}")

    return jwt.decode(
        token,
        crypto_key,
        algorithms=["RS256", "ES256"],
        audience=expected_audience,
        issuer=issuer,
        options={"require": ["exp", "aud", "iss", "sub"]},
    )


def clear_jwks_cache() -> None:
    """Clear the in-process JWKS cache (tests)."""
    _jwks_cache.clear()
