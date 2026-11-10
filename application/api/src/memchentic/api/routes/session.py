"""Session routes: registration, login, refresh, logout and browser OAuth."""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.api.schemas import LoginIn, RegisterIn
from memchentic.auth.oauth_browser import finish_oauth_login, start_oauth_login
from memchentic.auth.principal import Principal, check_admin_password
from memchentic.auth.rate_limit import check_rate_limit, client_ip
from memchentic.auth.sessions import (
    SESSION_COOKIE,
    SESSION_COOKIE_PATH,
    create_session,
    revoke_session,
    set_session_cookie,
    touch_session,
)
from memchentic.auth.tokens import ACCESS_TOKEN_SECONDS, issue_access_token
from memchentic.auth.users import authenticate_internal
from memchentic.deps import db_session, get_optional_principal
from memchentic.services import auth_roster as auth_roster_service
from memchentic.services import instance_settings as instance_settings_service
from memchentic.services.users import ensure_admin_user, register_internal_user
from memchentic.settings import get_settings

router = APIRouter()


GUI_CLIENT_HEADER = "X-Memchentic-Client"


@router.post("/auth/register")
async def register(
    request: Request,
    body: RegisterIn,
    session: AsyncSession = Depends(db_session),
    principal: Principal | None = Depends(get_optional_principal),
) -> dict:
    """Register an internal user.

    Internal mode only. Open while self-registration is enabled; otherwise
    administrators only. Rate limited per client. Errors: 400 outside internal
    mode, 403 when registration is closed, 429.
    """
    settings = get_settings()
    ip = client_ip(request)
    check_rate_limit(f"register:{ip}", limit=10, window_seconds=60)
    if settings.oauth_provider != "internal":
        raise HTTPException(400, "Registration only in internal mode")
    if not await instance_settings_service.get_register_enabled(session) and (
        principal is None or not principal.is_admin
    ):
        raise HTTPException(403, "Registration is disabled")
    try:
        user = await register_internal_user(
            session, email=body.email, password=body.password, display_name=body.display_name
        )
    except ValueError as exc:
        raise HTTPException(400, "Unable to register") from exc
    return {"id": str(user.id), "email": user.email, "display_name": user.display_name}


def _clear_session_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        SESSION_COOKIE,
        path=SESSION_COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )


def _require_gui_header(request: Request) -> None:
    """Reject cookie-authenticated calls lacking the GUI's custom header.

    A custom header forces a CORS preflight, so other origins cannot replay
    the session cookie even if SameSite is bypassed.
    """
    if request.headers.get(GUI_CLIENT_HEADER) != "gui":
        raise HTTPException(403, "Missing client header")


async def _session_tokens(
    session: AsyncSession, *, subject: str, email: str | None, provider: str
) -> dict[str, Any]:
    token = await issue_access_token(session, subject=subject, email=email, provider=provider)
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": ACCESS_TOKEN_SECONDS,
    }


@router.post("/auth/login")
async def login(
    request: Request,
    body: LoginIn,
    response: Response,
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Sign in with email and password and return an access token.

    Public. Sets a GUI session cookie. Rate limited per client and per account.
    Errors: 400 outside internal mode, 401 on bad credentials, 429.
    """
    settings = get_settings()
    ip = client_ip(request)
    # Per-client cap first: a new email must not give a new allowance.
    check_rate_limit(f"login-ip:{ip}", limit=60, window_seconds=60)
    # Per-account cap. Same normalisation as the account lookup
    # (normalize_email: strip + casefold). Hashed so the key has a fixed size.
    email_key = hashlib.sha256(body.email.strip().casefold().encode("utf-8", "replace")).hexdigest()
    check_rate_limit(f"login:{ip}:{email_key}", limit=20, window_seconds=60)
    # Prefer a real DB user when email+password match. ensure_admin_user() syncs
    # the ADMIN_EMAIL row with ADMIN_PASSWORD at startup, so the environment admin password
    # branch below is reached only if that row was changed after startup (for
    # example its email was rewritten by an IdP sign-in). It re-ensures the row.
    user = await authenticate_internal(session, email=body.email, password=body.password)
    if user:
        subject, email, provider, user_id = str(user.id), user.email, user.provider, user.id
        extra = {
            "is_admin": await auth_roster_service.user_is_administrator(session, user.id),
            "id": str(user.id),
            "email": user.email,
            "display_name": user.display_name,
        }
    elif check_admin_password(body.email, body.password, settings):
        admin = await ensure_admin_user(session, settings)
        await auth_roster_service.ensure_auth_groups(session, admin)
        subject, email, provider, user_id = (
            str(admin.id),
            admin.email,
            "admin",
            admin.id,
        )
        extra = {
            "is_admin": True,
            "id": str(admin.id),
            "email": admin.email,
            "display_name": admin.display_name,
        }
    else:
        raise HTTPException(401, "Invalid credentials")

    raw, _ = await create_session(
        session,
        subject=subject,
        email=email,
        provider=provider,
        user_id=user_id,
        persistent=body.remember,
        settings=settings,
    )
    set_session_cookie(response, raw, body.remember, settings)
    tokens = await _session_tokens(session, subject=subject, email=email, provider=provider)
    return {**tokens, **extra}


@router.post("/auth/refresh", response_model=None)
async def refresh_session(
    request: Request, response: Response, session: AsyncSession = Depends(db_session)
) -> dict | JSONResponse:
    """Mint a fresh access token from the session cookie.

    Returns:
        Access token payload, or a 401 that clears the cookie when the
        session is missing, revoked, or expired.
    """
    _require_gui_header(request)
    ip = client_ip(request)
    # The GUI refreshes once per page load and once per stream reconnect.
    # 120 per minute per client address leaves room for many users behind
    # one address and still bounds an anonymous loop.
    check_rate_limit(f"refresh:{ip}", limit=120, window_seconds=60)
    raw = request.cookies.get(SESSION_COOKIE)
    row = await touch_session(session, raw, get_settings()) if raw else None
    if not raw or row is None:
        denied = JSONResponse({"detail": "No active session"}, status_code=401)
        _clear_session_cookie(denied)
        return denied
    set_session_cookie(response, raw, row.persistent, get_settings())
    return await _session_tokens(
        session, subject=row.subject, email=row.email, provider=row.provider
    )


@router.post("/auth/logout")
async def logout(
    request: Request, response: Response, session: AsyncSession = Depends(db_session)
) -> dict[str, str]:
    """Revoke the browser session and clear its cookie."""
    _require_gui_header(request)
    raw = request.cookies.get(SESSION_COOKIE)
    if raw:
        await revoke_session(session, raw)
    _clear_session_cookie(response)
    return {"status": "ok"}


@router.get("/auth/oauth/start", response_model=None)
async def oauth_web_start(remember: bool = False) -> Response:
    """Begin browser OAuth against the configured external IdP."""
    return await start_oauth_login(remember=remember)


@router.get("/auth/oauth/callback", response_model=None)
async def oauth_web_callback(
    request: Request,
    session: AsyncSession = Depends(db_session),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    """Finish browser OAuth: upsert user, set session cookie, redirect home."""
    return await finish_oauth_login(request, session, code=code, state=state, error=error)
