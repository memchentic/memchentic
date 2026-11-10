"""Browser OAuth client: PKCE, authorize URL, code exchange and profile mapping."""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from memchentic.auth.claims import groups_from_claims
from memchentic.auth.jwks import fetch_jwks_and_decode
from memchentic.auth.providers import _oidc_discovery, resolve_scopes
from memchentic.settings import Settings


@dataclass
class OAuthProfile:
    """Normalized identity from an IdP code exchange."""

    subject: str
    email: str
    email_verified: bool
    display_name: str | None = None
    groups: frozenset[str] = frozenset()


def pkce_pair() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` for S256 PKCE."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


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
        "redirect_uri": settings.web_oauth_redirect_uri,
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
        "redirect_uri": settings.web_oauth_redirect_uri,
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
