"""OAuth provider presets, OIDC discovery, JWKS lookup and IdP code exchange."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

from memchentic.settings import Settings

_DISCOVERY_TTL_SECONDS = 300
_JWKS_TTL_SECONDS = 300
_discovery_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_jwks_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_jwks_uri_cache: dict[str, tuple[float, str]] = {}


@dataclass
class ProviderPreset:
    """Defaults for one ``OAUTH_PROVIDER`` value.

    Attributes:
        default_issuer: Issuer used when ``OAUTH_ISSUER`` is empty.
        default_scopes: Comma-separated scopes used when ``OAUTH_SCOPES`` is empty.
        discovery: False when the provider publishes no OIDC discovery document.
        label: Text for the Continue-with button.
    """

    default_issuer: str = ""
    default_scopes: str = "openid,profile,email"
    discovery: bool = True
    label: str = ""


@dataclass
class OAuthProfile:
    """Normalized identity from an IdP code exchange."""

    subject: str
    email: str
    email_verified: bool
    display_name: str | None = None
    groups: frozenset[str] = frozenset()


PRESETS: dict[str, ProviderPreset] = {
    "google": ProviderPreset(default_issuer="https://accounts.google.com", label="Google"),
    # GitHub is an OAuth App: web login only, opaque tokens, no OIDC discovery.
    "github": ProviderPreset(
        default_issuer="https://github.com/login/oauth",
        default_scopes="read:user,user:email",
        discovery=False,
        label="GitHub",
    ),
    "okta": ProviderPreset(label="Okta"),
    "authentik": ProviderPreset(label="Authentik"),
    "keycloak": ProviderPreset(label="Keycloak"),
    "auth0": ProviderPreset(label="Auth0"),
    "entra": ProviderPreset(
        default_scopes="openid,profile,email,offline_access", label="Microsoft"
    ),
    "gitlab": ProviderPreset(default_issuer="https://gitlab.com", label="GitLab"),
    "generic": ProviderPreset(label="SSO"),
    "internal": ProviderPreset(label="Internal"),
}


def resolve_issuer(settings: Settings) -> str:
    """Return the issuer for the active provider.

    Args:
        settings: App settings.

    Returns:
        ``OAUTH_ISSUER``, else the preset default; the session issuer in internal mode.
    """
    if settings.oauth_provider == "internal":
        return settings.session_issuer
    preset = PRESETS.get(settings.oauth_provider)
    return settings.oauth_issuer or (preset.default_issuer if preset else "")


def resolve_scopes(settings: Settings) -> str:
    """Return configured scopes, falling back to the active preset defaults."""
    raw = settings.oauth_scopes.strip()
    if raw:
        return raw
    preset = PRESETS.get(settings.oauth_provider)
    return preset.default_scopes if preset else "openid,profile,email"


def provider_label(settings: Settings) -> str:
    """Human label for the Continue-with button."""
    preset = PRESETS.get(settings.oauth_provider)
    if preset and preset.label:
        return preset.label
    return settings.oauth_provider.replace("_", " ").title()


def resolve_jwks_uri(settings: Settings) -> str:
    """Return the JWKS URL without OIDC discovery.

    Args:
        settings: App settings.

    Returns:
        ``OAUTH_JWKS_URI`` when set, otherwise a provider-specific or
        issuer-relative default.
    """
    if settings.oauth_jwks_uri:
        return settings.oauth_jwks_uri
    issuer = resolve_issuer(settings).rstrip("/")
    if settings.oauth_provider == "google":
        return "https://www.googleapis.com/oauth2/v3/certs"
    return f"{issuer}/.well-known/jwks.json"


async def discover_jwks_uri(settings: Settings) -> str:
    """Return the JWKS URL for the active external IdP.

    Order: an explicit ``OAUTH_JWKS_URI``; the ``jwks_uri`` of the pinned OIDC
    discovery document; the preset guess from :func:`resolve_jwks_uri`. The
    result, including the fallback, is cached for ``_DISCOVERY_TTL_SECONDS``
    so a failing discovery endpoint is not retried on every request.

    Args:
        settings: App settings.

    Returns:
        Absolute JWKS URL.
    """
    if settings.oauth_jwks_uri:
        return settings.oauth_jwks_uri
    issuer = resolve_issuer(settings).rstrip("/")
    now = time.monotonic()
    cached = _jwks_uri_cache.get(issuer)
    if cached and cached[0] > now:
        return cached[1]
    uri = ""
    preset = PRESETS.get(settings.oauth_provider)
    if preset is None or preset.discovery:
        try:
            meta = await _oidc_discovery(settings)
            uri = str(meta.get("jwks_uri") or "")
        except (httpx.HTTPError, ValueError):
            uri = ""
    if not uri:
        uri = resolve_jwks_uri(settings)
    _jwks_uri_cache[issuer] = (now + _DISCOVERY_TTL_SECONDS, uri)
    return uri


def _same_origin(issuer: str, url: str) -> bool:
    """Return True when ``url`` shares scheme+host+port with ``issuer``."""
    a = urlparse(issuer)
    b = urlparse(url)
    if not a.scheme or not a.netloc or not b.scheme or not b.netloc:
        return False

    def _origin(parsed: Any) -> tuple[str, str, int | None]:
        host = (parsed.hostname or "").lower()
        port = parsed.port
        if port is None:
            if parsed.scheme == "https":
                port = 443
            elif parsed.scheme == "http":
                port = 80
        return (parsed.scheme.lower(), host, port)

    return _origin(a) == _origin(b)


# Hosts, other than the issuer host, that a preset's genuine discovery document
# points at. Keyed by OAUTH_PROVIDER. Only https on the default port is accepted.
_EXTRA_DISCOVERY_HOSTS: dict[str, frozenset[str]] = {
    "google": frozenset(
        {
            "oauth2.googleapis.com",
            "openidconnect.googleapis.com",
            "www.googleapis.com",
        }
    ),
    "entra": frozenset({"graph.microsoft.com"}),
}


def _pin_discovery_origins(issuer: str, data: dict[str, Any], *, provider: str = "") -> None:
    """Reject discovery endpoints that leave the issuer origin.

    An endpoint passes when it shares scheme, host and port with the issuer,
    or when it is an ``https`` URL on the default port whose host is listed in
    ``_EXTRA_DISCOVERY_HOSTS`` for ``provider``.

    Args:
        issuer: Configured issuer URL.
        data: Parsed OIDC discovery document.
        provider: Active ``OAUTH_PROVIDER`` preset name.

    Raises:
        ValueError: When an endpoint is on a host that is neither the issuer
            origin nor allowlisted for the provider.
    """
    extra_hosts = _EXTRA_DISCOVERY_HOSTS.get(provider, frozenset())
    for key in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint", "jwks_uri"):
        raw = data.get(key)
        if not raw:
            continue
        url = str(raw)
        if _same_origin(issuer, url):
            continue
        parsed = urlparse(url)
        try:
            port = parsed.port
        except ValueError:
            port = -1
        if (
            parsed.scheme == "https"
            and (parsed.hostname or "").lower() in extra_hosts
            and port in (None, 443)
        ):
            continue
        raise ValueError(f"OIDC discovery {key} origin mismatch with issuer")


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


async def authorization_server_metadata(settings: Settings) -> dict:
    """Return RFC 8414 authorization-server metadata for the active provider.

    Internal mode describes the built-in authorization server. External mode
    relays the endpoints of the IdP's pinned OIDC discovery document, so a
    client that reads this document is sent to endpoints that exist.

    Args:
        settings: App settings.

    Returns:
        Metadata document.

    Raises:
        LookupError: When the external provider has no OIDC discovery.
        ValueError: When discovery is misconfigured, incomplete, or fails the origin pin.
        httpx.HTTPError: When the discovery document cannot be fetched.
    """
    base = settings.base_url.rstrip("/")
    if settings.oauth_provider == "internal":
        return {
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token",
            "jwks_uri": f"{base}/oauth/jwks",
            "registration_endpoint": f"{base}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": ["openid", "profile", "email"],
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["RS256"],
        }

    preset = PRESETS.get(settings.oauth_provider)
    if preset is not None and not preset.discovery:
        raise LookupError(
            f"Provider {settings.oauth_provider} publishes no authorization-server metadata"
        )
    meta = await _oidc_discovery(settings)
    authorization_endpoint = meta.get("authorization_endpoint")
    token_endpoint = meta.get("token_endpoint")
    if not authorization_endpoint or not token_endpoint:
        raise ValueError("OIDC discovery missing authorization or token endpoint")
    scopes = [s.strip() for s in resolve_scopes(settings).split(",") if s.strip()]
    document: dict[str, Any] = {
        "issuer": str(meta.get("issuer") or resolve_issuer(settings)),
        "authorization_endpoint": str(authorization_endpoint),
        "token_endpoint": str(token_endpoint),
        "response_types_supported": meta.get("response_types_supported") or ["code"],
        "grant_types_supported": meta.get("grant_types_supported")
        or ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": meta.get("code_challenge_methods_supported")
        or ["S256"],
        "scopes_supported": scopes,
    }
    for key in ("jwks_uri", "registration_endpoint", "token_endpoint_auth_methods_supported"):
        if meta.get(key):
            document[key] = meta[key]
    return document


def protected_resource_metadata(settings: Settings) -> dict:
    """Build RFC 9728 metadata for the MCP endpoint.

    Args:
        settings: App settings.

    Returns:
        The protected-resource metadata document.
    """
    issuer = (
        resolve_issuer(settings)
        if settings.oauth_provider != "internal"
        else settings.session_issuer
    )
    scopes = [s.strip() for s in resolve_scopes(settings).split(",") if s.strip()]
    return {
        "resource": settings.mcp_resource_url,
        "authorization_servers": [issuer],
        "scopes_supported": scopes,
        "bearer_methods_supported": ["header"],
    }


def pkce_pair() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` for S256 PKCE."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


async def _oidc_discovery(settings: Settings) -> dict[str, Any]:
    issuer = resolve_issuer(settings).rstrip("/")
    if not issuer:
        raise ValueError("OAUTH_ISSUER is required for this provider")
    now = time.monotonic()
    cached = _discovery_cache.get(issuer)
    if cached and cached[0] > now:
        return cached[1]
    url = f"{issuer}/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
    _pin_discovery_origins(issuer, data, provider=settings.oauth_provider)
    _discovery_cache[issuer] = (now + _DISCOVERY_TTL_SECONDS, data)
    return data


async def resolve_oidc_endpoints(settings: Settings) -> dict[str, str]:
    """Resolve authorize/token/userinfo endpoints for the active provider.

    Args:
        settings: App settings.

    Returns:
        Dict with authorization_endpoint, token_endpoint, and optional userinfo_endpoint.

    Raises:
        ValueError: When the provider cannot be resolved.
    """
    if settings.oauth_provider == "github":
        return {
            "authorization_endpoint": "https://github.com/login/oauth/authorize",
            "token_endpoint": "https://github.com/login/oauth/access_token",
            "userinfo_endpoint": "https://api.github.com/user",
        }
    if settings.oauth_provider == "internal":
        raise ValueError("Internal provider has no external authorize endpoints")
    meta = await _oidc_discovery(settings)
    auth = meta.get("authorization_endpoint")
    token = meta.get("token_endpoint")
    if not auth or not token:
        raise ValueError("OIDC discovery missing authorization or token endpoint")
    return {
        "authorization_endpoint": str(auth),
        "token_endpoint": str(token),
        "userinfo_endpoint": str(meta.get("userinfo_endpoint") or ""),
    }


async def build_authorize_url(
    settings: Settings,
    *,
    state: str,
    code_challenge: str,
    nonce: str | None = None,
) -> str:
    """Build the IdP authorization URL for browser Sign-in.

    Args:
        settings: App settings with client id and scopes.
        state: Opaque CSRF state.
        code_challenge: S256 PKCE challenge.
        nonce: Optional OIDC nonce (included for non-GitHub providers).

    Returns:
        Absolute authorize URL.
    """
    endpoints = await resolve_oidc_endpoints(settings)
    scopes = resolve_scopes(settings).replace(",", " ")
    params: dict[str, str] = {
        "client_id": settings.oauth_client_id,
        "redirect_uri": settings.web_oauth_redirect_uri(),
        "response_type": "code",
        "scope": scopes,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if settings.oauth_provider != "github":
        params["access_type"] = "online"
        if nonce:
            params["nonce"] = nonce
    return f"{endpoints['authorization_endpoint']}?{urlencode(params)}"


async def exchange_code_for_profile(
    settings: Settings,
    *,
    code: str,
    code_verifier: str,
    nonce: str | None = None,
) -> OAuthProfile:
    """Exchange an authorization code for a normalized user profile.

    Args:
        settings: App settings.
        code: Authorization code from the IdP callback.
        code_verifier: PKCE verifier from the start cookie.
        nonce: Optional OIDC nonce that must match the verified id_token.

    Returns:
        Normalized profile with verified email when available.

    Raises:
        ValueError: When the IdP response lacks a usable verified email/subject.
        httpx.HTTPError: When the token or profile HTTP call fails.
    """
    if settings.oauth_provider == "github":
        return await _github_exchange(settings, code=code, code_verifier=code_verifier)
    return await _oidc_exchange(settings, code=code, code_verifier=code_verifier, nonce=nonce)


async def _token_post(
    settings: Settings,
    *,
    token_endpoint: str,
    code: str,
    code_verifier: str,
) -> dict[str, Any]:
    """Exchange an authorization code at the IdP token endpoint.

    Args:
        settings: App settings (client id/secret, redirect URI).
        token_endpoint: IdP token endpoint URL.
        code: Authorization code from the callback.
        code_verifier: PKCE verifier stored for this login.

    Returns:
        The JSON token response.
    """
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.web_oauth_redirect_uri(),
        "client_id": settings.oauth_client_id,
        "code_verifier": code_verifier,
    }
    if settings.oauth_client_secret:
        data["client_secret"] = settings.oauth_client_secret
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(token_endpoint, data=data, headers={"Accept": "application/json"})
        resp.raise_for_status()
        return resp.json()


async def _oidc_exchange(
    settings: Settings,
    *,
    code: str,
    code_verifier: str,
    nonce: str | None = None,
) -> OAuthProfile:
    endpoints = await resolve_oidc_endpoints(settings)
    token_payload = await _token_post(
        settings,
        token_endpoint=endpoints["token_endpoint"],
        code=code,
        code_verifier=code_verifier,
    )
    claims: dict[str, Any] = {}
    id_token = token_payload.get("id_token")
    if isinstance(id_token, str) and id_token:
        claims = await fetch_jwks_and_decode(id_token, settings, audience=settings.oauth_client_id)
        if nonce is not None and claims.get("nonce") != nonce:
            raise ValueError("OIDC nonce mismatch")
    access_token = token_payload.get("access_token")
    userinfo_url = endpoints.get("userinfo_endpoint") or ""
    if userinfo_url and isinstance(access_token, str) and access_token:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                userinfo_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            info = resp.json()
            claims = {**claims, **info}
    return _profile_from_claims(claims)


async def _github_exchange(settings: Settings, *, code: str, code_verifier: str) -> OAuthProfile:
    endpoints = await resolve_oidc_endpoints(settings)
    token_payload = await _token_post(
        settings,
        token_endpoint=endpoints["token_endpoint"],
        code=code,
        code_verifier=code_verifier,
    )
    access_token = token_payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise ValueError("GitHub token response missing access_token")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        user_resp = await client.get("https://api.github.com/user", headers=headers)
        user_resp.raise_for_status()
        user = user_resp.json()
        emails_resp = await client.get("https://api.github.com/user/emails", headers=headers)
        emails_resp.raise_for_status()
        emails = emails_resp.json()
    subject = str(user.get("id") or "")
    if not subject:
        raise ValueError("GitHub profile missing id")
    email = ""
    verified = False
    if isinstance(emails, list):
        primary_verified = next(
            (
                e
                for e in emails
                if isinstance(e, dict) and e.get("primary") and e.get("verified") and e.get("email")
            ),
            None,
        )
        any_verified = next(
            (e for e in emails if isinstance(e, dict) and e.get("verified") and e.get("email")),
            None,
        )
        chosen = primary_verified or any_verified
        if chosen:
            email = str(chosen["email"])
            verified = True
    if not email:
        public = user.get("email")
        if isinstance(public, str) and public:
            email = public
            verified = False
    display = user.get("name") or user.get("login")
    return OAuthProfile(
        subject=subject,
        email=email,
        email_verified=verified,
        display_name=str(display) if display else None,
    )


def _profile_from_claims(claims: dict[str, Any]) -> OAuthProfile:
    from memchentic.auth.idp_groups import groups_from_claims

    subject = str(claims.get("sub") or "").strip()
    email = str(claims.get("email") or "").strip()
    raw_verified = claims.get("email_verified", False)
    if isinstance(raw_verified, str):
        verified = raw_verified.lower() in {"1", "true", "yes"}
    else:
        verified = bool(raw_verified)
    display = claims.get("name") or claims.get("preferred_username")
    if not subject:
        raise ValueError("OAuth subject is required")
    if not email:
        raise ValueError("OAuth email is required")
    return OAuthProfile(
        subject=subject,
        email=email,
        email_verified=verified,
        display_name=str(display) if display else None,
        groups=frozenset(groups_from_claims(claims)),
    )


def clear_discovery_cache() -> None:
    """Clear the in-process OIDC discovery and JWKS-URL caches (tests)."""
    _discovery_cache.clear()
    _jwks_uri_cache.clear()


def clear_jwks_cache() -> None:
    """Clear the in-process JWKS cache (tests)."""
    _jwks_cache.clear()
