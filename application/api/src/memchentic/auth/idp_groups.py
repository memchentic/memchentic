"""IdP directory group listing and claim extraction for Auth Group binds."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from memchentic.auth.providers import resolve_issuer
from memchentic.auth.web_oauth import resolve_oidc_endpoints
from memchentic.settings import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IdPGroupOption:
    """One selectable IdP group for an Auth Group bind dropdown."""

    id: str
    name: str


@dataclass
class IdPGroupListResult:
    """Outcome of listing IdP groups for the active OAuth provider."""

    provider: str
    supports_groups: bool
    groups: list[IdPGroupOption]
    error: str | None = None


def provider_supports_groups(provider: str) -> bool:
    """True when this OAuth provider has a groups directory adapter."""
    return provider in _LISTERS


def _same_host_https(base_url: str, next_url: str | None) -> str | None:
    """Return ``next_url`` only when it is https on the same host as ``base_url``.

    Args:
        base_url: The directory URL already called (scheme+host trusted).
        next_url: Pagination link from the provider response.

    Returns:
        The next URL, or ``None`` when missing or off-host / non-https.
    """
    if not next_url or not isinstance(next_url, str):
        return None
    base = urlparse(base_url)
    nxt = urlparse(next_url)
    if nxt.scheme != "https":
        return None
    if (nxt.hostname or "").casefold() != (base.hostname or "").casefold():
        return None
    return next_url


async def list_idp_groups(settings: Settings) -> IdPGroupListResult:
    """List directory groups using the configured OAuth client credentials.

    Args:
        settings: App settings (provider + client id/secret + issuer).

    Returns:
        Provider metadata, options, and an optional error message.
    """
    provider = settings.oauth_provider
    if not provider_supports_groups(provider):
        return IdPGroupListResult(
            provider=provider,
            supports_groups=False,
            groups=[],
            error=None,
        )
    if not settings.oauth_client_id.strip() or not settings.oauth_client_secret.strip():
        return IdPGroupListResult(
            provider=provider,
            supports_groups=True,
            groups=[],
            error="OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET are required to list groups",
        )
    try:
        groups = await _LISTERS[provider](settings)
        return IdPGroupListResult(
            provider=provider,
            supports_groups=True,
            groups=groups,
            error=None,
        )
    except ValueError as exc:
        # Raised by this module for missing or malformed configuration; safe to show.
        return IdPGroupListResult(
            provider=provider,
            supports_groups=True,
            groups=[],
            error=str(exc) or "Failed to list IdP groups",
        )
    except Exception:
        # IdP/network failure becomes a UI error, not a 500.
        logger.warning("IdP group listing failed for provider %s", provider, exc_info=True)
        return IdPGroupListResult(
            provider=provider,
            supports_groups=True,
            groups=[],
            error="Failed to list IdP groups; see the server log",
        )


def _okta_org_base(issuer: str) -> str:
    """Derive ``https://{org}.okta.com`` (or oktapreview) from an OIDC issuer."""
    parsed = urlparse(issuer.rstrip("/"))
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("OAUTH_ISSUER is required for Okta group listing")
    return f"{parsed.scheme}://{parsed.netloc}"


async def _client_credentials_token(
    token_url: str,
    *,
    client_id: str,
    client_secret: str,
    scope: str | None = None,
    audience: str | None = None,
) -> str:
    data: dict[str, str] = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if scope:
        data["scope"] = scope
    if audience:
        data["audience"] = audience
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(token_url, data=data)
        resp.raise_for_status()
        payload = resp.json()
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise ValueError("Client-credentials response missing access_token")
    return token


async def _list_okta(settings: Settings) -> list[IdPGroupOption]:
    issuer = resolve_issuer(settings)
    org = _okta_org_base(issuer)
    # Okta API access via the same OIDC app (API Services / custom auth server).
    token_url = f"{org}/oauth2/v1/token"
    try:
        token = await _client_credentials_token(
            token_url,
            client_id=settings.oauth_client_id,
            client_secret=settings.oauth_client_secret,
            scope="okta.groups.read",
        )
    except httpx.HTTPError:
        # Fall back to the configured OIDC issuer token endpoint when present.
        endpoints = await resolve_oidc_endpoints(settings)
        token = await _client_credentials_token(
            str(endpoints["token_endpoint"]),
            client_id=settings.oauth_client_id,
            client_secret=settings.oauth_client_secret,
            scope="okta.groups.read",
        )
    groups: list[IdPGroupOption] = []
    url: str | None = f"{org}/api/v1/groups?limit=200"
    async with httpx.AsyncClient(timeout=30.0) as client:
        while url:
            resp = await client.get(
                url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}
            )
            resp.raise_for_status()
            rows = resp.json()
            if not isinstance(rows, list):
                break
            for row in rows:
                if not isinstance(row, dict):
                    continue
                gid = str(row.get("id") or "").strip()
                raw_profile = row.get("profile")
                profile: dict[str, Any] = raw_profile if isinstance(raw_profile, dict) else {}
                name = str(profile.get("name") or gid).strip()
                if gid:
                    groups.append(IdPGroupOption(id=gid, name=name or gid))
            link = resp.headers.get("link") or ""
            next_url = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    next_url = part[part.find("<") + 1 : part.find(">")]
                    break
            url = _same_host_https(url, next_url)
    return groups


async def _list_entra(settings: Settings) -> list[IdPGroupOption]:
    token = await _client_credentials_token(
        "https://login.microsoftonline.com/organizations/oauth2/v2.0/token"
        if "organizations" in (settings.oauth_issuer or "")
        else _entra_token_url(settings),
        client_id=settings.oauth_client_id,
        client_secret=settings.oauth_client_secret,
        scope="https://graph.microsoft.com/.default",
    )
    groups: list[IdPGroupOption] = []
    url: str | None = "https://graph.microsoft.com/v1.0/groups?$select=id,displayName&$top=999"
    async with httpx.AsyncClient(timeout=30.0) as client:
        while url:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            resp.raise_for_status()
            payload = resp.json()
            for row in payload.get("value") or []:
                if not isinstance(row, dict):
                    continue
                gid = str(row.get("id") or "").strip()
                name = str(row.get("displayName") or gid).strip()
                if gid:
                    groups.append(IdPGroupOption(id=gid, name=name or gid))
            url = _same_host_https(url, payload.get("@odata.nextLink"))
    return groups


def _entra_token_url(settings: Settings) -> str:
    issuer = resolve_issuer(settings).rstrip("/")
    if issuer:
        # https://login.microsoftonline.com/{tenant}/v2.0 → token endpoint
        if issuer.endswith("/v2.0"):
            return f"{issuer}/token"
        return f"{issuer}/oauth2/v2.0/token"
    return "https://login.microsoftonline.com/common/oauth2/v2.0/token"


async def _list_authentik(settings: Settings) -> list[IdPGroupOption]:
    issuer = resolve_issuer(settings).rstrip("/")
    if not issuer:
        raise ValueError("OAUTH_ISSUER is required for Authentik group listing")
    parsed = urlparse(issuer)
    base = f"{parsed.scheme}://{parsed.netloc}"
    endpoints = await resolve_oidc_endpoints(settings)
    token = await _client_credentials_token(
        str(endpoints["token_endpoint"]),
        client_id=settings.oauth_client_id,
        client_secret=settings.oauth_client_secret,
    )
    groups: list[IdPGroupOption] = []
    url: str | None = f"{base}/api/v3/core/groups/?page_size=200"
    async with httpx.AsyncClient(timeout=30.0) as client:
        while url:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
            resp.raise_for_status()
            payload = resp.json()
            results = payload.get("results") if isinstance(payload, dict) else payload
            if not isinstance(results, list):
                break
            for row in results:
                if not isinstance(row, dict):
                    continue
                gid = str(row.get("pk") or row.get("id") or "").strip()
                name = str(row.get("name") or gid).strip()
                if gid:
                    groups.append(IdPGroupOption(id=gid, name=name or gid))
            next_link = payload.get("next") if isinstance(payload, dict) else None
            url = _same_host_https(url, next_link if isinstance(next_link, str) else None)
    return groups


async def _list_keycloak(settings: Settings) -> list[IdPGroupOption]:
    issuer = resolve_issuer(settings).rstrip("/")
    if not issuer or "/realms/" not in issuer:
        raise ValueError("OAUTH_ISSUER must be a Keycloak realm issuer")
    realm_root = issuer
    # issuer like https://host/realms/myrealm
    token_url = f"{realm_root}/protocol/openid-connect/token"
    token = await _client_credentials_token(
        token_url,
        client_id=settings.oauth_client_id,
        client_secret=settings.oauth_client_secret,
    )
    # Admin API base: strip /realms/{realm}
    host = issuer.split("/realms/")[0]
    realm = issuer.split("/realms/")[1].split("/")[0]
    url = f"{host}/admin/realms/{realm}/groups?briefRepresentation=true"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        rows = resp.json()
    groups: list[IdPGroupOption] = []

    def walk(nodes: list[Any]) -> None:
        for row in nodes:
            if not isinstance(row, dict):
                continue
            gid = str(row.get("id") or "").strip()
            name = str(row.get("name") or path_name(row) or gid).strip()
            if gid:
                groups.append(IdPGroupOption(id=gid, name=name or gid))
            children = row.get("subGroups")
            if isinstance(children, list):
                walk(children)

    def path_name(row: dict[str, Any]) -> str:
        path = row.get("path")
        return str(path) if isinstance(path, str) else ""

    if isinstance(rows, list):
        walk(rows)
    return groups


async def _list_auth0(settings: Settings) -> list[IdPGroupOption]:
    issuer = resolve_issuer(settings).rstrip("/")
    if not issuer:
        raise ValueError("OAUTH_ISSUER is required for Auth0 group listing")
    # Roles via Management API (Auth0 "groups" often map to roles).
    audience = f"{issuer}/api/v2/"
    token = await _client_credentials_token(
        f"{issuer}/oauth/token",
        client_id=settings.oauth_client_id,
        client_secret=settings.oauth_client_secret,
        audience=audience,
    )
    groups: list[IdPGroupOption] = []
    url = f"{issuer}/api/v2/roles?per_page=100&page=0&include_totals=false"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        rows = resp.json()
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            gid = str(row.get("id") or "").strip()
            name = str(row.get("name") or gid).strip()
            if gid:
                groups.append(IdPGroupOption(id=gid, name=name or gid))
    return groups


# Providers that can list directory groups via the configured OAuth client.
_LISTERS: dict[str, Callable[[Settings], Awaitable[list[IdPGroupOption]]]] = {
    "okta": _list_okta,
    "entra": _list_entra,
    "authentik": _list_authentik,
    "keycloak": _list_keycloak,
    "auth0": _list_auth0,
}
