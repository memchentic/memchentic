"""OAuth provider presets, OIDC discovery, JWKS lookup and IdP code exchange."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from memchentic.settings import Settings

_DISCOVERY_TTL_SECONDS = 300
_discovery_cache: dict[str, tuple[float, dict[str, Any]]] = {}
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


def clear_discovery_cache() -> None:
    """Clear the in-process OIDC discovery and JWKS-URL caches (tests)."""
    _discovery_cache.clear()
    _jwks_uri_cache.clear()
