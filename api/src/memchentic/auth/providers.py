from __future__ import annotations

from dataclasses import dataclass

import httpx
from jose import jwt

from memchentic.settings import Settings


@dataclass
class ProviderPreset:
    name: str
    default_issuer: str = ""
    default_scopes: str = "openid,profile,email"
    jwks_path: str = "/.well-known/jwks.json"
    discovery: bool = True
    notes: str = ""


PRESETS: dict[str, ProviderPreset] = {
    "google": ProviderPreset(
        name="google",
        default_issuer="https://accounts.google.com",
        default_scopes="openid,profile,email",
        notes="Google OAuth / Workspace",
    ),
    "github": ProviderPreset(
        name="github",
        default_issuer="https://github.com/login/oauth",
        default_scopes="read:user,user:email",
        discovery=False,
        notes="GitHub OAuth App — often needs OAuth proxy",
    ),
    "okta": ProviderPreset(name="okta", notes="Okta OIDC — set OAUTH_ISSUER to org/auth server"),
    "authentik": ProviderPreset(
        name="authentik",
        notes="Self-hosted Authentik application issuer",
    ),
    "keycloak": ProviderPreset(name="keycloak", notes="Keycloak realm issuer"),
    "auth0": ProviderPreset(name="auth0", notes="Auth0 tenant issuer"),
    "entra": ProviderPreset(
        name="entra",
        default_scopes="openid,profile,email,offline_access",
        notes="Microsoft Entra ID",
    ),
    "gitlab": ProviderPreset(
        name="gitlab",
        default_issuer="https://gitlab.com",
        notes="GitLab OAuth/OIDC",
    ),
    "generic": ProviderPreset(name="generic", notes="Raw OIDC via issuer + client env"),
    "internal": ProviderPreset(name="internal", notes="Built-in email/password AS"),
}


def resolve_issuer(settings: Settings) -> str:
    if settings.oauth_provider == "internal":
        return settings.memchentic_base_url.rstrip("/")
    preset = PRESETS.get(settings.oauth_provider)
    return settings.oauth_issuer or (preset.default_issuer if preset else "")


def resolve_jwks_uri(settings: Settings) -> str:
    if settings.oauth_jwks_uri:
        return settings.oauth_jwks_uri
    issuer = resolve_issuer(settings).rstrip("/")
    if settings.oauth_provider == "google":
        return "https://www.googleapis.com/oauth2/v3/certs"
    # OIDC discovery fallback path
    return f"{issuer}/.well-known/jwks.json"


async def fetch_jwks_and_decode(token: str, settings: Settings) -> dict:
    jwks_uri = resolve_jwks_uri(settings)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(jwks_uri)
        resp.raise_for_status()
        jwks = resp.json()

    header = jwt.get_unverified_header(token)
    kid = header.get("kid")
    key = None
    for k in jwks.get("keys", []):
        if not kid or k.get("kid") == kid:
            key = k
            break
    if key is None:
        raise ValueError("No matching JWKS key")

    return jwt.decode(
        token,
        key,
        algorithms=[header.get("alg", "RS256")],
        audience=settings.effective_audience,
        issuer=resolve_issuer(settings) or None,
        options={"verify_aud": bool(settings.effective_audience), "verify_iss": bool(resolve_issuer(settings))},
    )


def authorization_server_metadata(settings: Settings) -> dict:
    base = settings.memchentic_base_url.rstrip("/")
    if settings.oauth_provider != "internal":
        issuer = resolve_issuer(settings)
        return {
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/authorize",
            "token_endpoint": f"{issuer}/token",
            "jwks_uri": resolve_jwks_uri(settings),
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": settings.oauth_scopes.split(","),
        }
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "jwks_uri": f"{base}/oauth/jwks",
        "registration_endpoint": f"{base}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
        "scopes_supported": ["openid", "profile", "email"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }


def protected_resource_metadata(settings: Settings) -> dict:
    base = settings.memchentic_base_url.rstrip("/")
    issuer = resolve_issuer(settings) if settings.oauth_provider != "internal" else base
    return {
        "resource": settings.mcp_resource_url,
        "authorization_servers": [issuer],
        "scopes_supported": settings.oauth_scopes.split(","),
        "bearer_methods_supported": ["header"],
    }
