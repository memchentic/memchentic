# Auth design

## Goals

- Cursor / Claude **Authorize** against remote MCP (MCP OAuth / protected resource metadata).
- Day-1 presets for common IdPs; one active provider per deployment via env.
- Optional **`internal`** mode: memchentic itself is the authorization server (login/register/consent).
- Optional API keys bound to a user for automation.

## Env contract (shape)

```bash
# Which adapter is active (exactly one)
OAUTH_PROVIDER=google|github|okta|authentik|keycloak|auth0|entra|gitlab|generic|internal

# Standard OAuth client (used by external providers; also usable by harnesses
# that need a static client id against internal)
OAUTH_CLIENT_ID=
OAUTH_CLIENT_SECRET=          # optional for public+PKCE clients

# Discovery / validation
OAUTH_ISSUER=                 # e.g. https://accounts.google.com  or Authentik app issuer
OAUTH_AUDIENCE=               # resource / API audience (often the MCP public URL)
OAUTH_JWKS_URI=               # optional override; else discover from issuer metadata
OAUTH_SCOPES=openid,profile,email   # optional; preset defaults apply if unset

# Public URL of this memchentic instance (for redirects, PRM, internal AS)
MEMCHENTIC_BASE_URL=https://mcp.example.com

# Optional API key gate (dual-accept with OAuth when set)
# MEMCHENTIC_API_KEY=         # or per-user keys in DB later
```

Preset adapters mainly fill discovery quirks (Google/GitHub/Okta/Authentik issuer
paths, default scopes, DCR vs static client notes). **`generic`** = raw OIDC with
the shared env vars only.

### `internal`

When `OAUTH_PROVIDER=internal`:

- memchentic serves authorization-server metadata, authorize, token, JWKS
- local user accounts (email/password or invite bootstrap — TBD in impl)
- still exposes MCP resource metadata so Cursor Authorize works against *this*
  base URL with no external IdP

External IdP remains the recommended production path for homelab (Authentik);
`internal` is for standalone demos and single-box installs.

## Runtime behavior

1. Unauthenticated MCP call → `401` + `WWW-Authenticate` with
   `resource_metadata` pointing at `/.well-known/oauth-protected-resource`.
2. PRM lists `authorization_servers` = configured issuer (external or self).
3. Valid Bearer JWT (issuer/audience/signature) → request principal → personal
   workspace (+ shared memberships).
4. If API key present and valid → same principal binding.
5. Never trust client body fields for ACL.

## Day-1 provider list

| `OAUTH_PROVIDER` | Notes |
| --- | --- |
| `google` | Google OAuth / Workspace |
| `github` | GitHub OAuth App |
| `okta` | Okta OIDC |
| `authentik` | Self-hosted Authentik app issuer |
| `keycloak` | Keycloak realm issuer |
| `auth0` | Auth0 tenant |
| `entra` | Microsoft Entra ID |
| `gitlab` | GitLab OAuth/OIDC |
| `generic` | Any OIDC AS via issuer + client env |
| `internal` | Built-in AS |

More presets are cheap registry entries later (Ping, OneLogin, JumpCloud, …).
