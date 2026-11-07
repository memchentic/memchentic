from __future__ import annotations

import base64
import hashlib
import html
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import check_admin_password
from memchentic.auth.providers import (
    authorization_server_metadata,
    protected_resource_metadata,
)
from memchentic.auth.tokens import (
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


class RegisterBody(BaseModel):
    email: EmailStr
    password: str
    display_name: str | None = None


class LoginBody(BaseModel):
    email: EmailStr
    password: str


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/{path:path}")
async def prm(path: str = "") -> dict:
    return protected_resource_metadata(get_settings())


@router.get("/.well-known/oauth-authorization-server")
async def as_meta() -> dict:
    return authorization_server_metadata(get_settings())


@router.get("/oauth/jwks")
async def oauth_jwks(session: AsyncSession = Depends(db_session)) -> dict:
    await ensure_signing_key(session)
    return await jwks(session)


@router.post("/oauth/register")
async def dynamic_client_register(
    body: dict,
    session: AsyncSession = Depends(db_session),
) -> dict:
    client_id = f"mcp_{secrets.token_urlsafe(12)}"
    redirect_uris = body.get("redirect_uris") or []
    if isinstance(redirect_uris, str):
        redirect_uris = [redirect_uris]
    client = OAuthClient(
        id=uuid.uuid4(),
        client_id=client_id,
        client_secret_hash=None,
        redirect_uris=list(redirect_uris),
        is_public=True,
        name=body.get("client_name") or "MCP Client",
    )
    session.add(client)
    await session.commit()
    return {
        "client_id": client_id,
        "client_id_issued_at": int(datetime.now(UTC).timestamp()),
        "token_endpoint_auth_method": "none",
        "redirect_uris": redirect_uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }


_PKCE_CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
_FRAME_DENY = {"X-Frame-Options": "DENY", "Content-Security-Policy": "frame-ancestors 'none'"}


def _authorize_page(
    client: OAuthClient, fields: dict[str, str], error: str | None = None
) -> str:
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
<label>Email or admin user</label>
<input name="email" type="text" required autocomplete="username" />
<label>Password</label>
<input name="password" type="password" required autocomplete="current-password" />
{hidden}<button type="submit">Authorize</button>
<p style="margin-top:1rem;font-size:.85rem">No account? Register in the UI, then return here.</p>
</form></body></html>"""


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
    if not client or redirect_uri not in (client.redirect_uris or []):
        raise HTTPException(400, "Unknown client_id or unregistered redirect_uri")
    if client.is_public and (
        code_challenge_method != "S256" or not _PKCE_CHALLENGE.match(code_challenge)
    ):
        raise HTTPException(400, "PKCE S256 with a valid code_challenge is required")
    return client


@router.get("/oauth/authorize")
async def authorize_get(
    response_type: str = Query("code"),
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    scope: str = Query("openid profile email"),
    state: str | None = None,
    code_challenge: str | None = None,
    code_challenge_method: str | None = None,
    session: AsyncSession = Depends(db_session),
) -> HTMLResponse:
    """Show the internal sign-in page for a validated authorization request."""
    settings = get_settings()
    if settings.oauth_provider != "internal":
        raise HTTPException(400, "Internal authorize only when OAUTH_PROVIDER=internal")
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
    return HTMLResponse(_authorize_page(client, fields), headers=_FRAME_DENY)


@router.post("/oauth/authorize", response_model=None)
async def authorize_post(
    email: str = Form(...),
    password: str = Form(...),
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    scope: str = Form("openid profile email"),
    state: str = Form(""),
    code_challenge: str = Form(""),
    code_challenge_method: str = Form(""),
    session: AsyncSession = Depends(db_session),
) -> RedirectResponse | HTMLResponse:
    """Authenticate the user and redirect back to the client with a one-time code."""
    settings = get_settings()
    client = await _require_registered_client(
        session, client_id, redirect_uri, code_challenge, code_challenge_method
    )
    user_id: uuid.UUID | None = None
    if check_admin_password(email, password, settings):
        user_id = None
    else:
        user = await authenticate_internal(session, email=email, password=password)
        if not user:
            fields = {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": scope,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": code_challenge_method,
            }
            page = _authorize_page(client, fields, "Invalid credentials")
            return HTMLResponse(page, status_code=401, headers=_FRAME_DENY)
        user_id = user.id

    code = new_opaque_token()
    auth_code = AuthCode(
        id=uuid.uuid4(),
        code=code,
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


@router.post("/oauth/token")
async def token_endpoint(
    grant_type: str = Form(...),
    code: str | None = Form(None),
    redirect_uri: str | None = Form(None),
    client_id: str | None = Form(None),
    code_verifier: str | None = Form(None),
    refresh_token: str | None = Form(None),
    session: AsyncSession = Depends(db_session),
) -> JSONResponse:
    settings = get_settings()
    if grant_type == "authorization_code":
        if not code or not client_id:
            raise HTTPException(400, "code and client_id required")
        result = await session.execute(select(AuthCode).where(AuthCode.code == code))
        auth_code = result.scalar_one_or_none()
        if not auth_code or auth_code.consumed_at or auth_code.expires_at < datetime.now(UTC):
            raise HTTPException(400, "Invalid code")
        if auth_code.client_id != client_id:
            raise HTTPException(400, "client_id mismatch")
        if auth_code.redirect_uri != redirect_uri:
            raise HTTPException(400, "redirect_uri mismatch")
        client_row = await session.execute(
            select(OAuthClient).where(OAuthClient.client_id == client_id)
        )
        client = client_row.scalar_one_or_none()
        if not client:
            raise HTTPException(400, "Unknown client_id")
        if (client.is_public or auth_code.code_challenge) and not (
            auth_code.code_challenge
            and code_verifier
            and _verify_pkce(
                code_verifier, auth_code.code_challenge, auth_code.code_challenge_method or ""
            )
        ):
            raise HTTPException(400, "PKCE verification failed")
        auth_code.consumed_at = datetime.now(UTC)
        if auth_code.user_id is None:
            subject, email_claim, provider = "admin", settings.admin_contact_email, "admin"
        else:
            subject, email_claim, provider = str(auth_code.user_id), None, "internal"
        access = await issue_access_token(
            session,
            subject=subject,
            email=email_claim,
            provider=provider,
            settings=settings,
        )
        refresh = new_opaque_token()
        session.add(
            AuthRefreshToken(
                id=uuid.uuid4(),
                token_hash=hash_token(refresh),
                client_id=client_id,
                user_id=auth_code.user_id,
                scope=auth_code.scope,
                expires_at=datetime.now(UTC) + timedelta(days=30),
            )
        )
        await session.commit()
        return JSONResponse(
            {
                "access_token": access,
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": refresh,
                "scope": auth_code.scope,
            }
        )

    if grant_type == "refresh_token":
        if not refresh_token or not client_id:
            raise HTTPException(400, "refresh_token and client_id required")
        result = await session.execute(
            select(AuthRefreshToken).where(
                AuthRefreshToken.token_hash == hash_token(refresh_token),
                AuthRefreshToken.revoked_at.is_(None),
            )
        )
        row = result.scalar_one_or_none()
        if not row or row.expires_at < datetime.now(UTC) or row.client_id != client_id:
            raise HTTPException(400, "Invalid refresh token")
        if row.user_id is None:
            subject, email_claim, provider = "admin", settings.admin_contact_email, "admin"
        else:
            subject, email_claim, provider = str(row.user_id), None, "internal"
        access = await issue_access_token(
            session,
            subject=subject,
            email=email_claim,
            provider=provider,
            settings=settings,
        )
        return JSONResponse(
            {
                "access_token": access,
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": row.scope,
            }
        )

    raise HTTPException(400, f"Unsupported grant_type: {grant_type}")
