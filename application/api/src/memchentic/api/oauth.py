from __future__ import annotations

import base64
import hashlib
import html
import re
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import check_admin_password
from memchentic.auth.providers import (
    authorization_server_metadata,
    protected_resource_metadata,
)
from memchentic.auth.rate_limit import check_rate_limit, client_ip
from memchentic.auth.tokens import (
    ACCESS_TOKEN_SECONDS,
    ensure_signing_key,
    hash_token,
    issue_access_token,
    jwks,
    new_opaque_token,
)
from memchentic.auth.users import authenticate_internal
from memchentic.deps import db_session
from memchentic.models.entities import AuthCode, AuthRefreshToken, OAuthClient
from memchentic.settings import get_settings

router = APIRouter(tags=["oauth"])

_OAUTH_CSRF_COOKIE = "memchentic_oauth_csrf"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_SCHEME_NAME = re.compile(r"^[a-z][a-z0-9+.-]*$")
_MAX_REDIRECT_URI_LEN = 1024  # auth_codes.redirect_uri is String(1024)
_MAX_REDIRECT_URIS = 10
_MAX_CLIENT_NAME_LEN = 255  # oauth_clients.name is String(255)
_MAX_SCOPE_LEN = 512  # auth_codes.scope is String(512)
_UNUSED_CLIENT_RETENTION = timedelta(hours=24)
_EXPIRED_CODE_RETENTION = timedelta(days=1)
_PRUNE_INTERVAL_SECONDS = 3600.0
_prune_state = {"last": 0.0}


async def prune_stale_oauth_rows(session: AsyncSession, *, force: bool = False) -> None:
    """Delete expired authorization codes and clients that never completed a sign-in.

    Runs at most once per ``_PRUNE_INTERVAL_SECONDS`` per process. A client is
    stale when it is older than ``_UNUSED_CLIENT_RETENTION`` and no refresh
    token and no authorization code references it. Refresh-token rows are never
    deleted, so a client that has completed one sign-in is never stale. The
    caller commits.

    Args:
        session: Database session.
        force: Ignore the once-per-interval guard (tests).
    """
    now = time.monotonic()
    if not force and _prune_state["last"] and now - _prune_state["last"] < _PRUNE_INTERVAL_SECONDS:
        return
    _prune_state["last"] = now
    wall = datetime.now(UTC)
    await session.execute(
        text("DELETE FROM auth_codes WHERE expires_at < :cutoff"),
        {"cutoff": wall - _EXPIRED_CODE_RETENTION},
    )
    await session.execute(
        text(
            """
            DELETE FROM oauth_clients AS c
            WHERE c.created_at < :cutoff
              AND NOT EXISTS (
                  SELECT 1 FROM auth_refresh_tokens r WHERE r.client_id = c.client_id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM auth_codes a WHERE a.client_id = c.client_id
              )
            """
        ),
        {"cutoff": wall - _UNUSED_CLIENT_RETENTION},
    )


def validate_redirect_uri(uri: str) -> str:
    """Validate a redirect URI for dynamic client registration.

    Allows ``https://`` URLs without userinfo or fragment, and loopback
    ``http://`` only for hosts in {127.0.0.1, localhost, [::1], ::1}.
    Private-use schemes listed in ``OAUTH_NATIVE_REDIRECT_SCHEMES`` are
    accepted for native clients.

    Args:
        uri: Candidate redirect URI string.

    Returns:
        The same URI when valid.

    Raises:
        HTTPException: 400 when the URI is empty, not a string, too long,
            contains whitespace, control characters or a backslash, is
            relative, or uses a disallowed scheme/host/userinfo/fragment.
    """
    if not uri or not isinstance(uri, str) or not uri.strip():
        raise HTTPException(400, "Invalid redirect_uri: empty")
    if len(uri) > _MAX_REDIRECT_URI_LEN:
        raise HTTPException(
            400, f"Invalid redirect_uri: longer than {_MAX_REDIRECT_URI_LEN} characters"
        )
    if any(ch.isspace() or ch == "\\" or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in uri):
        raise HTTPException(
            400,
            "Invalid redirect_uri: whitespace, control characters and backslashes are not allowed",
        )
    try:
        parsed = urlparse(uri)
    except ValueError as exc:
        raise HTTPException(400, "Invalid redirect_uri: not a valid URL") from exc
    scheme = (parsed.scheme or "").lower()
    if not scheme:
        raise HTTPException(400, "Invalid redirect_uri: relative URIs are not allowed")
    if scheme in {"javascript", "data", "file"}:
        raise HTTPException(400, f"Invalid redirect_uri: scheme {scheme!r} is not allowed")
    if parsed.fragment:
        raise HTTPException(400, "Invalid redirect_uri: fragment is not allowed")
    if parsed.username is not None or parsed.password is not None:
        raise HTTPException(400, "Invalid redirect_uri: userinfo is not allowed")
    if scheme == "https":
        if not parsed.hostname:
            raise HTTPException(400, "Invalid redirect_uri: https host required")
        return uri
    if scheme == "http":
        host = (parsed.hostname or "").lower()
        if host not in _LOOPBACK_HOSTS:
            raise HTTPException(
                400,
                "Invalid redirect_uri: http is only allowed for loopback "
                "(127.0.0.1, localhost, ::1)",
            )
        return uri
    native_schemes = {
        name
        for name in (
            part.strip().lower() for part in get_settings().oauth_native_redirect_schemes.split(",")
        )
        if _SCHEME_NAME.match(name)
    }
    if scheme in native_schemes:
        if not (parsed.netloc or parsed.path):
            raise HTTPException(
                400, "Invalid redirect_uri: a private-use URI needs a host or a path"
            )
        return uri
    raise HTTPException(400, f"Invalid redirect_uri: scheme {scheme!r} is not allowed")


# ``path`` is bound by the "/{path:path}" route variant and is not used.
@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/{path:path}")
async def prm(path: str = "") -> dict:  # noqa: ARG001
    return protected_resource_metadata(get_settings())


@router.get("/.well-known/oauth-authorization-server")
async def as_meta() -> dict:
    """Serve RFC 8414 metadata; relays the external IdP's endpoints when one is configured."""
    try:
        return await authorization_server_metadata(get_settings())
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(503, "Identity provider discovery is unavailable") from exc


@router.get("/oauth/jwks")
async def oauth_jwks(session: AsyncSession = Depends(db_session)) -> dict:
    await ensure_signing_key(session)
    return await jwks(session)


@router.post("/oauth/register")
async def dynamic_client_register(
    request: Request,
    body: dict,
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Register a public OAuth client (RFC 7591) with the built-in authorization server.

    Args:
        request: Incoming request (client address for the rate limit).
        body: Client metadata; ``redirect_uris`` is required, ``client_name`` optional.
        session: Database session.

    Returns:
        The registered client metadata including the generated ``client_id``.

    Raises:
        HTTPException: 400 for an external provider or invalid metadata; 429 when
            the caller registers too often.
    """
    if get_settings().oauth_provider != "internal":
        raise HTTPException(
            400, "Dynamic client registration is only available when OAUTH_PROVIDER=internal"
        )
    check_rate_limit(f"dcr:{client_ip(request)}", limit=10, window_seconds=60)

    raw_uris = body.get("redirect_uris")
    if isinstance(raw_uris, str):
        raw_uris = [raw_uris]
    if not isinstance(raw_uris, list) or not raw_uris:
        raise HTTPException(400, "redirect_uris must be a non-empty array of strings")
    if len(raw_uris) > _MAX_REDIRECT_URIS:
        raise HTTPException(400, f"redirect_uris: at most {_MAX_REDIRECT_URIS} entries")
    validated = [validate_redirect_uri(u) for u in raw_uris]

    raw_name = body.get("client_name")
    if raw_name is not None and not isinstance(raw_name, str):
        raise HTTPException(400, "client_name must be a string")
    name = (raw_name or "").strip()[:_MAX_CLIENT_NAME_LEN] or "MCP Client"

    await prune_stale_oauth_rows(session)
    client_id = f"mcp_{secrets.token_urlsafe(12)}"
    session.add(
        OAuthClient(
            id=uuid.uuid4(),
            client_id=client_id,
            client_secret_hash=None,
            redirect_uris=list(validated),
            is_public=True,
            name=name,
        )
    )
    await session.commit()
    return {
        "client_id": client_id,
        "client_id_issued_at": int(datetime.now(UTC).timestamp()),
        "client_name": name,
        "token_endpoint_auth_method": "none",
        "redirect_uris": validated,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }


_PKCE_CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_FRAME_DENY = {"X-Frame-Options": "DENY", "Content-Security-Policy": "frame-ancestors 'none'"}


def _authorize_page(client: OAuthClient, fields: dict[str, str], error: str | None = None) -> str:
    """Render the internal sign-in page naming the requesting client and redirect host.

    Args:
        client: The registered client asking for authorization.
        fields: Authorization request parameters re-posted as hidden inputs.
        error: Optional error message to show above the form.

    Returns:
        The escaped HTML document.
    """
    err = f"<p style='color:#f88'>{html.escape(error)}</p>" if error else ""
    host = urlparse(fields["redirect_uri"]).netloc or fields["redirect_uri"]
    hidden = "".join(
        f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}" />'
        for k, v in fields.items()
    )
    return f"""<!doctype html>
<html><head><title>memchentic Authorize</title>
<style>
body{{font-family:system-ui;background:#111;color:#ddd;display:flex;min-height:100vh;\
align-items:center;justify-content:center}}
form{{background:#1a1a1a;border:1px solid #333;padding:2rem;width:360px}}
input{{width:100%;margin:.4rem 0 1rem;padding:.6rem;background:#0d0d0d;border:1px solid #444;\
color:#eee}}
button{{width:100%;padding:.7rem;background:#2a2a2a;border:1px solid #555;color:#fff;\
cursor:pointer}}
h1{{font-size:1.2rem;margin:0 0 1rem}}
code{{color:#fff}}
</style></head><body>
<form method="post">
<h1>memchentic</h1>
<p><code>{html.escape(client.name or "Unnamed client")}</code> wants access to your memory.</p>
<p>After sign-in you will be sent to <code>{html.escape(host)}</code>.
Only continue if you started this from that app.</p>
{err}
<label>Email</label>
<input name="email" type="email" required autocomplete="username" />
<label>Password</label>
<input name="password" type="password" required autocomplete="current-password" />
{hidden}<button type="submit">Authorize</button>
<p style="margin-top:1rem;font-size:.85rem">No account? Register in the UI, then return here.</p>
</form></body></html>"""


def _authorize_html(
    client: OAuthClient,
    fields: dict[str, str],
    *,
    request: Request | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Build an authorize HTML response with a fresh CSRF cookie + hidden field."""
    settings = get_settings()
    csrf = secrets.token_urlsafe(24)
    page_fields = {**fields, "csrf_token": csrf}
    response = HTMLResponse(
        _authorize_page(client, page_fields, error),
        status_code=status_code,
        headers=_FRAME_DENY,
    )
    # Secure when this request arrived over https, or when the public BASE_URL
    # is https (TLS terminated at a reverse proxy, so the socket scheme seen
    # here is http). A plain-http BASE_URL keeps the cookie usable over http.
    secure = (
        request is not None and request.url.scheme == "https"
    ) or settings.base_url.lower().startswith("https://")
    response.set_cookie(
        _OAUTH_CSRF_COOKIE,
        csrf,
        httponly=True,
        samesite="strict",
        path="/oauth",
        secure=secure,
    )
    return response


async def _require_registered_client(
    session: AsyncSession,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
) -> OAuthClient:
    """Reject unknown clients, unregistered redirect URIs, and public clients without PKCE.

    Args:
        session: Database session.
        client_id: OAuth client id from the request.
        redirect_uri: Redirect URI from the request; must exactly match a registered URI.
        code_challenge: PKCE challenge from the request.
        code_challenge_method: PKCE method from the request.

    Returns:
        The registered client.

    Raises:
        HTTPException: 400 when the client, redirect URI, or PKCE input is not acceptable.
    """
    result = await session.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))
    client = result.scalar_one_or_none()
    if (
        not client
        or len(redirect_uri) > _MAX_REDIRECT_URI_LEN
        or redirect_uri not in (client.redirect_uris or [])
    ):
        raise HTTPException(400, "Unknown client_id or unregistered redirect_uri")
    if client.is_public and (
        code_challenge_method != "S256" or not _PKCE_CHALLENGE.match(code_challenge)
    ):
        raise HTTPException(400, "PKCE S256 with a valid code_challenge is required")
    return client


def _require_internal_provider() -> None:
    if get_settings().oauth_provider != "internal":
        raise HTTPException(400, "Internal authorize only when OAUTH_PROVIDER=internal")


@router.get("/oauth/authorize")
async def authorize_get(
    request: Request,
    response_type: str = Query("code"),
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    scope: str = Query("openid profile email", max_length=_MAX_SCOPE_LEN),
    state: str | None = None,
    code_challenge: str | None = None,
    code_challenge_method: str | None = None,
    session: AsyncSession = Depends(db_session),
) -> HTMLResponse:
    """Show the internal sign-in page for a validated authorization request."""
    _require_internal_provider()
    if response_type != "code":
        raise HTTPException(400, "Only response_type=code supported")
    client = await _require_registered_client(
        session, client_id, redirect_uri, code_challenge or "", code_challenge_method or ""
    )
    fields = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state or "",
        "code_challenge": code_challenge or "",
        "code_challenge_method": code_challenge_method or "",
    }
    return _authorize_html(client, fields, request=request)


@router.post("/oauth/authorize", response_model=None)
async def authorize_post(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    scope: str = Form("openid profile email", max_length=_MAX_SCOPE_LEN),
    state: str = Form(""),
    code_challenge: str = Form(""),
    code_challenge_method: str = Form(""),
    csrf_token: str = Form(""),
    session: AsyncSession = Depends(db_session),
) -> RedirectResponse | HTMLResponse:
    """Authenticate the user and redirect back to the client with a one-time code."""
    _require_internal_provider()
    check_rate_limit(f"authorize:{client_ip(request)}", limit=20, window_seconds=60)
    cookie_csrf = request.cookies.get(_OAUTH_CSRF_COOKIE, "")
    # Compare bytes: compare_digest raises TypeError on non-ASCII str input.
    if (
        not csrf_token
        or not cookie_csrf
        or not secrets.compare_digest(
            csrf_token.encode("utf-8", "surrogatepass"),
            cookie_csrf.encode("utf-8", "surrogatepass"),
        )
    ):
        raise HTTPException(400, "Invalid CSRF token")

    settings = get_settings()
    client = await _require_registered_client(
        session, client_id, redirect_uri, code_challenge, code_challenge_method
    )
    # Prefer a real DB user when email+password match; break-glass binds to
    # the ensured ADMIN_EMAIL user so tokens always carry a user id.
    user = await authenticate_internal(session, email=email, password=password)
    user_id: uuid.UUID | None
    if user:
        user_id = user.id
    elif check_admin_password(email, password, settings):
        from memchentic.auth.users import ensure_admin_user

        user_id = (await ensure_admin_user(session, settings)).id
    else:
        fields = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": code_challenge_method,
        }
        return _authorize_html(
            client, fields, request=request, error="Invalid credentials", status_code=401
        )

    code = new_opaque_token()
    auth_code = AuthCode(
        id=uuid.uuid4(),
        code_hash=hash_token(code),
        client_id=client_id,
        user_id=user_id,
        redirect_uri=redirect_uri,
        scope=scope,
        code_challenge=code_challenge or None,
        code_challenge_method=code_challenge_method or None,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    session.add(auth_code)
    await session.commit()

    params = {"code": code}
    if state:
        params["state"] = state
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}{urlencode(params)}", status_code=302)


def _verify_pkce(code_verifier: str, challenge: str, method: str) -> bool:
    if method != "S256":
        return False
    digest = hashlib.sha256(code_verifier.encode()).digest()
    calc = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return calc == challenge


async def _revoke_refresh_family(session: AsyncSession, family_id: uuid.UUID) -> None:
    await session.execute(
        update(AuthRefreshToken)
        .where(
            AuthRefreshToken.family_id == family_id,
            AuthRefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )


_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _token_error(error: str, description: str) -> JSONResponse:
    """Build an RFC 6749 section 5.2 error response for the token endpoint.

    Args:
        error: Registered error code, for example ``invalid_grant``.
        description: Human-readable detail for the client developer.

    Returns:
        JSON 400 response that must not be cached.
    """
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=400,
        headers=_NO_STORE,
    )


@router.post("/oauth/token")
async def token_endpoint(
    request: Request,
    grant_type: str | None = Form(None),
    code: str | None = Form(None),
    redirect_uri: str | None = Form(None),
    client_id: str | None = Form(None),
    code_verifier: str | None = Form(None),
    refresh_token: str | None = Form(None),
    session: AsyncSession = Depends(db_session),
) -> JSONResponse:
    """Exchange an authorization code or a refresh token for tokens (RFC 6749 section 5)."""
    check_rate_limit(f"token:{client_ip(request)}", limit=20, window_seconds=60)
    settings = get_settings()
    if not grant_type:
        return _token_error("invalid_request", "grant_type is required")

    if grant_type == "authorization_code":
        if not code or not client_id:
            return _token_error("invalid_request", "code and client_id are required")
        result = await session.execute(
            text(
                """
                UPDATE auth_codes
                SET consumed_at = now()
                WHERE code_hash = :h
                  AND consumed_at IS NULL
                  AND expires_at > now()
                RETURNING id, client_id, user_id, redirect_uri, scope,
                          code_challenge, code_challenge_method
                """
            ),
            {"h": hash_token(code)},
        )
        row = result.mappings().first()
        if not row:
            await session.rollback()
            return _token_error("invalid_grant", "Invalid code")
        if row["client_id"] != client_id:
            await session.rollback()
            return _token_error("invalid_grant", "client_id mismatch")
        if row["redirect_uri"] != redirect_uri:
            await session.rollback()
            return _token_error("invalid_grant", "redirect_uri mismatch")
        client_row = await session.execute(
            select(OAuthClient).where(OAuthClient.client_id == client_id)
        )
        client = client_row.scalar_one_or_none()
        if not client:
            await session.rollback()
            return _token_error("invalid_client", "Unknown client_id")
        if (client.is_public or row["code_challenge"]) and not (
            row["code_challenge"]
            and code_verifier
            and _verify_pkce(
                code_verifier, row["code_challenge"], row["code_challenge_method"] or ""
            )
        ):
            await session.rollback()
            return _token_error("invalid_grant", "PKCE verification failed")
        if row["user_id"] is None:
            await session.rollback()
            return _token_error("invalid_grant", "Invalid code")
        subject, email_claim, provider = str(row["user_id"]), None, "internal"
        token_user_id = row["user_id"]
        access = await issue_access_token(
            session,
            subject=subject,
            email=email_claim,
            provider=provider,
            settings=settings,
        )
        refresh = new_opaque_token()
        family_id = uuid.uuid4()
        session.add(
            AuthRefreshToken(
                id=uuid.uuid4(),
                token_hash=hash_token(refresh),
                family_id=family_id,
                client_id=client_id,
                user_id=token_user_id,
                scope=row["scope"],
                expires_at=datetime.now(UTC) + timedelta(days=30),
            )
        )
        await session.commit()
        return JSONResponse(
            {
                "access_token": access,
                "token_type": "Bearer",
                "expires_in": ACCESS_TOKEN_SECONDS,
                "refresh_token": refresh,
                "scope": row["scope"],
            },
            headers=_NO_STORE,
        )

    if grant_type == "refresh_token":
        if not refresh_token or not client_id:
            return _token_error("invalid_request", "refresh_token and client_id are required")
        token_h = hash_token(refresh_token)
        # Peek first for reuse detection (already-revoked presented again).
        existing = (
            await session.execute(
                select(AuthRefreshToken).where(AuthRefreshToken.token_hash == token_h)
            )
        ).scalar_one_or_none()
        if (
            existing is not None
            and existing.revoked_at is not None
            and existing.client_id == client_id
        ):
            await _revoke_refresh_family(session, existing.family_id)
            await session.commit()
            return _token_error("invalid_grant", "Invalid refresh token")

        # Atomic consume: only one concurrent refresh wins the row.
        consumed = (
            (
                await session.execute(
                    text(
                        """
                    UPDATE auth_refresh_tokens
                    SET revoked_at = now()
                    WHERE token_hash = :h
                      AND revoked_at IS NULL
                      AND expires_at > now()
                      AND client_id = :cid
                    RETURNING id, family_id, user_id, scope
                    """
                    ),
                    {"h": token_h, "cid": client_id},
                )
            )
            .mappings()
            .first()
        )
        if not consumed or consumed["user_id"] is None:
            await session.rollback()
            return _token_error("invalid_grant", "Invalid refresh token")

        new_refresh = new_opaque_token()
        session.add(
            AuthRefreshToken(
                id=uuid.uuid4(),
                token_hash=hash_token(new_refresh),
                family_id=consumed["family_id"],
                client_id=client_id,
                user_id=consumed["user_id"],
                scope=consumed["scope"],
                expires_at=datetime.now(UTC) + timedelta(days=30),
            )
        )
        access = await issue_access_token(
            session,
            subject=str(consumed["user_id"]),
            email=None,
            provider="internal",
            settings=settings,
        )
        await session.commit()
        return JSONResponse(
            {
                "access_token": access,
                "token_type": "Bearer",
                "expires_in": ACCESS_TOKEN_SECONDS,
                "refresh_token": new_refresh,
                "scope": consumed["scope"],
            },
            headers=_NO_STORE,
        )

    return _token_error("unsupported_grant_type", f"Unsupported grant_type: {grant_type}")
