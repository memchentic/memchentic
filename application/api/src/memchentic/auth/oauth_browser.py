"""Browser OAuth start/callback helpers for external IdP web Sign-in."""

from __future__ import annotations

import logging
import secrets
from typing import Any
from urllib.parse import urlencode

from fastapi import HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.providers import (
    build_authorize_url,
    exchange_code_for_profile,
    pkce_pair,
)
from memchentic.auth.sessions import (
    SESSION_COOKIE,
    SESSION_COOKIE_PATH,
    create_session,
    session_lifetime,
)
from memchentic.auth.users import upsert_oauth_user
from memchentic.settings import Settings, get_settings

logger = logging.getLogger(__name__)

OAUTH_STATE_COOKIE = "memchentic_oauth"
OAUTH_NONCE_COOKIE = "memchentic_oauth_nonce"
OAUTH_STATE_MAX_AGE = 600


def _encode_oauth_state(*, state: str, verifier: str, remember: bool) -> str:
    """Encode PKCE state for a cookie (url-safe parts, no JSON commas)."""
    return f"{state}.{verifier}.{1 if remember else 0}"


def _decode_oauth_state(raw: str) -> dict[str, Any] | None:
    parts = raw.split(".", 2)
    if len(parts) != 3:
        return None
    state, verifier, flag = parts
    if not state or not verifier or flag not in {"0", "1"}:
        return None
    return {"state": state, "verifier": verifier, "remember": flag == "1"}


def _set_oauth_state_cookie(
    response: Response, *, state: str, verifier: str, remember: bool, settings: Settings
) -> None:
    response.set_cookie(
        OAUTH_STATE_COOKIE,
        _encode_oauth_state(state=state, verifier=verifier, remember=remember),
        max_age=OAUTH_STATE_MAX_AGE,
        path=SESSION_COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _set_oauth_nonce_cookie(response: Response, *, nonce: str, settings: Settings) -> None:
    response.set_cookie(
        OAUTH_NONCE_COOKIE,
        nonce,
        max_age=OAUTH_STATE_MAX_AGE,
        path=SESSION_COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _clear_oauth_state_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        OAUTH_STATE_COOKIE,
        path=SESSION_COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _clear_oauth_nonce_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        OAUTH_NONCE_COOKIE,
        path=SESSION_COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _read_oauth_state(request: Request) -> dict[str, Any] | None:
    raw = request.cookies.get(OAUTH_STATE_COOKIE)
    if not raw:
        return None
    return _decode_oauth_state(raw)


def _set_session_cookie(response: Response, raw: str, persistent: bool, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        raw,
        max_age=(int(session_lifetime(True, settings).total_seconds()) if persistent else None),
        path=SESSION_COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )


async def start_oauth_login(
    *,
    remember: bool = False,
    settings: Settings | None = None,
) -> RedirectResponse:
    """Redirect the browser to the configured IdP authorize endpoint.

    Args:
        remember: Persist the session after callback when true.
        settings: Optional settings override.

    Returns:
        Redirect to the IdP with PKCE + state cookie set.

    Raises:
        HTTPException: When web OAuth is not configured.
    """
    settings = settings or get_settings()
    if not settings.web_oauth_enabled:
        raise HTTPException(400, "Web OAuth is not configured")
    state = secrets.token_urlsafe(24)
    verifier, challenge = pkce_pair()
    nonce = secrets.token_urlsafe(24) if settings.oauth_provider != "github" else None
    try:
        url = await build_authorize_url(
            settings, state=state, code_challenge=challenge, nonce=nonce
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    response = RedirectResponse(url, status_code=302)
    _set_oauth_state_cookie(
        response, state=state, verifier=verifier, remember=remember, settings=settings
    )
    if nonce:
        _set_oauth_nonce_cookie(response, nonce=nonce, settings=settings)
    return response


async def finish_oauth_login(
    request: Request,
    session: AsyncSession,
    *,
    code: str | None,
    state: str | None,
    error: str | None = None,
    settings: Settings | None = None,
) -> RedirectResponse:
    """Complete the IdP callback: upsert user, set session cookie, redirect home.

    Args:
        request: Incoming callback request (state cookie).
        session: Database session.
        code: Authorization code from the IdP.
        state: CSRF state from the IdP.
        error: Optional IdP error query param.
        settings: Optional settings override.

    Returns:
        Redirect to ``/`` on success, or ``/sign-in`` with an error query on failure.
    """
    settings = settings or get_settings()
    base = settings.base_url.rstrip("/")

    def fail(message: str) -> RedirectResponse:
        resp = RedirectResponse(f"{base}/sign-in?{urlencode({'error': message})}", status_code=302)
        _clear_oauth_state_cookie(resp, settings)
        _clear_oauth_nonce_cookie(resp, settings)
        return resp

    if error:
        return fail(error)
    if not settings.web_oauth_enabled:
        return fail("Web OAuth is not configured")
    if not code or not state:
        return fail("Missing authorization code")

    stored = _read_oauth_state(request)
    if not stored or stored.get("state") != state:
        return fail("Invalid OAuth state")
    verifier = stored.get("verifier")
    if not isinstance(verifier, str) or not verifier:
        return fail("Invalid OAuth state")
    remember = bool(stored.get("remember"))

    nonce = request.cookies.get(OAUTH_NONCE_COOKIE)
    if settings.oauth_provider != "github" and not nonce:
        return fail("Missing OAuth nonce")

    try:
        profile = await exchange_code_for_profile(
            settings, code=code, code_verifier=verifier, nonce=nonce
        )
        user = await upsert_oauth_user(
            session,
            provider=settings.oauth_provider,
            subject=profile.subject,
            email=profile.email,
            display_name=profile.display_name,
            email_verified=profile.email_verified,
            idp_groups=profile.groups,
        )
    except ValueError as exc:
        return fail(str(exc) or "OAuth login failed")
    except Exception:
        # IdP/network errors become a sign-in flash.
        logger.warning("OAuth callback failed", exc_info=True)
        return fail("OAuth login failed")

    raw, _ = await create_session(
        session,
        subject=str(user.id),
        email=user.email,
        provider=user.provider,
        user_id=user.id,
        persistent=remember,
        settings=settings,
    )
    response = RedirectResponse(f"{base}/", status_code=302)
    _set_session_cookie(response, raw, remember, settings)
    _clear_oauth_state_cookie(response, settings)
    _clear_oauth_nonce_cookie(response, settings)
    return response
