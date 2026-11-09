from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import Principal, check_admin_password, resolve_bearer
from memchentic.auth.rate_limit import check_rate_limit
from memchentic.auth.sessions import (
    SESSION_COOKIE,
    SESSION_COOKIE_PATH,
    create_session,
    revoke_session,
    session_lifetime,
    touch_session,
)
from memchentic.auth.tokens import ACCESS_TOKEN_SECONDS, issue_access_token
from memchentic.auth.users import (
    authenticate_internal,
    delete_user,
    directory_user_visible,
    get_user,
    get_user_by_email,
    list_directory_users,
    lookup_user_by_email,
    providers_for,
    register_internal_user,
    user_delete_eligibility,
)
from memchentic.db.session import get_session_factory, set_rls_context
from memchentic.deps import db_session, get_optional_principal, get_principal
from memchentic.events import subscribe_memory_events
from memchentic.models.entities import User
from memchentic.services import api_keys as api_key_service
from memchentic.services import auth_groups as auth_group_service
from memchentic.services import instance_settings as instance_settings_service
from memchentic.services import memories as memory_service
from memchentic.services import metrics as metrics_service
from memchentic.services import spaces as space_service
from memchentic.services.spaces import SpaceResolveError, Visibility
from memchentic.settings import get_settings

api_router = APIRouter(prefix="/api")

GUI_CLIENT_HEADER = "X-Memchentic-Client"


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    display_name: str | None = None


class LoginIn(BaseModel):
    email: str
    password: str
    remember: bool = False


class RememberIn(BaseModel):
    title: str
    body: str
    type: str = "note"
    tags: list[str] = []
    space_id: str | None = None
    group: str | None = None
    visibility: Visibility | None = None
    expires_at: str | None = None
    ttl_days: int | None = Field(default=None, ge=0)
    permanent: bool = False
    path_hint: str | None = None
    repo_hint: str | None = None
    force: bool = False


class MeIn(BaseModel):
    default_shared_space_id: str | None = None


class ReviseIn(BaseModel):
    title: str | None = None
    body: str | None = None
    type: str | None = None
    tags: list[str] | None = None
    expires_at: str | None = None
    clear_expiry: bool = False
    path_hint: str | None = None
    repo_hint: str | None = None


class GroupIn(BaseModel):
    name: str


class MemberIn(BaseModel):
    user_id: str | None = None
    email: str | None = None


class ApiKeyIn(BaseModel):
    name: str = "default"
    user_id: str | None = None


class ApiKeyCycleIn(BaseModel):
    """Replace a user's active keys with one new random secret."""

    user_id: str | None = None
    name: str = "default"


class GuideIn(BaseModel):
    title: str
    body: str
    tags: list[str] = []
    space_id: str | None = None
    group: str | None = None
    visibility: Visibility | None = None
    expires_at: str | None = None
    ttl_days: int | None = Field(default=None, ge=0)
    permanent: bool = False


def _uid(value: str | None) -> uuid.UUID | None:
    return uuid.UUID(value) if value else None


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


async def _memory_out(
    session: AsyncSession, memory: Any, *, full: bool = False
) -> dict:
    """Serialize a memory and attach creator email / display name."""
    rows = await memory_service.enrich_creator_fields(
        session, [memory_service.memory_to_dict(memory, full=full)]
    )
    return rows[0]


async def _memories_out(session: AsyncSession, memories: list[Any]) -> list[dict]:
    """Serialize memories and attach creator email / display name."""
    return await memory_service.enrich_creator_fields(
        session, [memory_service.memory_to_dict(m) for m in memories]
    )


async def _digests_out(session: AsyncSession, digests: list[Any]) -> list[dict]:
    """Serialize digests and attach creator email / display name."""
    return await memory_service.enrich_creator_fields(
        session, [memory_service.digest_to_dict(d) for d in digests]
    )


@api_router.get("/health")
async def health() -> dict[str, str]:
    from memchentic.embeddings import get_embedder
    from memchentic.settings import get_settings

    settings = get_settings()
    try:
        backend = get_embedder().backend
        embedder_ok = backend == "onnx" or settings.allow_hash_embeddings
    except RuntimeError:
        backend = "unavailable"
        embedder_ok = False
    status = "ok" if embedder_ok else "degraded"
    return {
        "status": status,
        "service": "memchentic",
        "embedder": backend,
    }


_SSE_REAUTH_SECONDS = 10.0


async def _reauth_memory_events(
    token: str | None,
) -> tuple[Principal, set[str]] | None:
    """Re-resolve bearer auth and readable space ids on a fresh DB session.

    Args:
        token: Original Authorization bearer token (JWT or API key).

    Returns:
        ``(principal, allowed_space_ids)`` when the token is still valid,
        otherwise ``None`` (caller should end the SSE stream).
    """
    factory = get_session_factory()
    async with factory() as session:
        principal = await resolve_bearer(session, token)
        if principal is None:
            return None
        await set_rls_context(session, user_id=principal.user_id, is_admin=principal.is_admin)
        allowed = {
            str(sid)
            for sid in await memory_service.accessible_space_ids(session, principal, None)
        }
        return principal, allowed


@api_router.get("/events/memories")
async def memory_events(request: Request) -> StreamingResponse:
    """SSE stream of memory mutations the caller is allowed to see.

    Auth is resolved before the stream starts so the request does not hold a
    pooled DB session open for the lifetime of the SSE connection. The stream
    re-calls ``resolve_bearer`` and refreshes readable space ids at least every
    10s (and on idle keepalive). Invalid tokens end the stream. Clients should
    reconnect on drop; the GUI also keeps a 30s safety poll.
    """
    auth = request.headers.get("Authorization") or ""
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else None
    initial = await _reauth_memory_events(token)
    if initial is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    principal, allowed = initial

    async def gen() -> AsyncIterator[bytes]:
        nonlocal principal, allowed
        last_reauth = time.monotonic()
        yield b": connected\n\n"
        async with subscribe_memory_events() as queue:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25.0)
                except TimeoutError:
                    refreshed = await _reauth_memory_events(token)
                    if refreshed is None:
                        return
                    principal, allowed = refreshed
                    last_reauth = time.monotonic()
                    yield b": keepalive\n\n"
                    continue
                now = time.monotonic()
                if now - last_reauth >= _SSE_REAUTH_SECONDS:
                    refreshed = await _reauth_memory_events(token)
                    if refreshed is None:
                        return
                    principal, allowed = refreshed
                    last_reauth = now
                space_id = str(event.get("space_id") or "")
                if space_id not in allowed:
                    continue
                data = json.dumps(event, separators=(",", ":"))
                yield f"event: memory\ndata: {data}\n\n".encode()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@api_router.post("/auth/register")
async def register(
    request: Request,
    body: RegisterIn,
    session: AsyncSession = Depends(db_session),
    principal: Principal | None = Depends(get_optional_principal),
) -> dict:
    settings = get_settings()
    ip = request.client.host if request.client else "unknown"
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


def _set_session_cookie(response: Response, raw: str, persistent: bool) -> None:
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        raw,
        max_age=(
            int(session_lifetime(True, settings).total_seconds()) if persistent else None
        ),
        path=SESSION_COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )


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


@api_router.post("/auth/login")
async def login(
    request: Request,
    body: LoginIn,
    response: Response,
    session: AsyncSession = Depends(db_session),
) -> dict:
    settings = get_settings()
    ip = request.client.host if request.client else "unknown"
    check_rate_limit(
        f"login:{ip}:{body.email.casefold()}", limit=20, window_seconds=60
    )
    # Prefer a real DB user when email+password match; break-glass is fallback.
    user = await authenticate_internal(session, email=body.email, password=body.password)
    if user:
        from memchentic.services.auth_groups import ensure_auth_groups, user_is_administrator

        await ensure_auth_groups(session, settings)
        subject, email, provider, user_id = str(user.id), user.email, user.provider, user.id
        extra = {
            "is_admin": await user_is_administrator(session, user.id),
            "id": str(user.id),
            "email": user.email,
            "display_name": user.display_name,
        }
    elif check_admin_password(body.email, body.password, settings):
        from memchentic.auth.users import ensure_admin_user
        from memchentic.services.auth_groups import ensure_auth_groups

        admin = await ensure_admin_user(session, settings)
        await ensure_auth_groups(session, settings)
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
    _set_session_cookie(response, raw, body.remember)
    tokens = await _session_tokens(session, subject=subject, email=email, provider=provider)
    return {**tokens, **extra}


@api_router.post("/auth/refresh", response_model=None)
async def refresh_session(
    request: Request, response: Response, session: AsyncSession = Depends(db_session)
) -> dict | JSONResponse:
    """Mint a fresh access token from the session cookie.

    Returns:
        Access token payload, or a 401 that clears the cookie when the
        session is missing, revoked, or expired.
    """
    _require_gui_header(request)
    raw = request.cookies.get(SESSION_COOKIE)
    row = await touch_session(session, raw, get_settings()) if raw else None
    if row is None:
        denied = JSONResponse({"detail": "No active session"}, status_code=401)
        _clear_session_cookie(denied)
        return denied
    _set_session_cookie(response, raw, row.persistent)
    return await _session_tokens(
        session, subject=row.subject, email=row.email, provider=row.provider
    )


@api_router.post("/auth/logout")
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


@api_router.get("/auth/oauth/start", response_model=None)
async def oauth_web_start(remember: bool = False) -> Response:
    """Begin browser OAuth against the configured external IdP."""
    from memchentic.auth.oauth_browser import start_oauth_login

    return await start_oauth_login(remember=remember)


@api_router.get("/auth/oauth/callback", response_model=None)
async def oauth_web_callback(
    request: Request,
    session: AsyncSession = Depends(db_session),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    """Finish browser OAuth: upsert user, set session cookie, redirect home."""
    from memchentic.auth.oauth_browser import finish_oauth_login

    return await finish_oauth_login(
        request, session, code=code, state=state, error=error
    )



@api_router.get("/me")
async def me(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    return await _me_payload(session, principal)


@api_router.patch("/me")
async def patch_me(
    body: MeIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    try:
        await space_service.set_default_shared_space(
            session, principal, _uid(body.default_shared_space_id)
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return await _me_payload(session, principal)


async def _me_payload(session: AsyncSession, principal: Principal) -> dict[str, Any]:
    spaces = await space_service.list_spaces(session, principal)
    personal = (
        await space_service.get_personal_space(session, principal.user_id)
        if principal.user_id
        else None
    )
    user = await session.get(User, principal.user_id) if principal.user_id else None
    default_shared = user.default_shared_space_id if user else None
    return {
        "default_shared_space_id": str(default_shared) if default_shared else None,
        "id": str(principal.user_id) if principal.user_id else None,
        "subject": principal.subject,
        "email": principal.email,
        "display_name": principal.display_name,
        "provider": principal.provider,
        "is_admin": principal.is_admin,
        "personal_space_id": str(personal.id) if personal else None,
        "memberships": [
            {"id": str(s.id), "name": s.name, "kind": s.kind.value} for s in spaces
        ],
    }


@api_router.get("/users")
async def users(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> list[dict]:
    """Authenticated directory for group invites; admins get extra metadata.

    Non-admins only see users who share at least one group space with them.
    """
    rows = await list_directory_users(session, principal)
    out: list[dict] = []
    for u in rows:
        row: dict = {
            "id": str(u.id),
            "email": u.email,
            "display_name": u.display_name,
        }
        if principal.is_admin:
            row["providers"] = providers_for(u)
            row["created_at"] = u.created_at.isoformat() if u.created_at else None
            deletable, reason = await user_delete_eligibility(
                session, actor_user_id=principal.user_id, target=u
            )
            row["deletable"] = deletable
            row["delete_blocked_reason"] = reason
        out.append(row)
    return out


@api_router.get("/users/lookup")
async def users_lookup(
    email: str = Query(..., min_length=1),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Exact email lookup for admins and group roster managers.

    Misses and unauthorized callers both receive 404 to avoid enumeration.
    """
    user = await lookup_user_by_email(session, principal, email)
    if user is None:
        raise HTTPException(404, "Not found")
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
    }


@api_router.get("/users/{user_id}")
async def show_user(
    user_id: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Directory detail plus groups the caller is allowed to know about."""
    try:
        uid = uuid.UUID(user_id)
    except ValueError as exc:
        raise HTTPException(404, "Not found") from exc
    if not await directory_user_visible(session, principal, uid):
        raise HTTPException(404, "Not found")
    user = await get_user(session, uid)
    if not user:
        raise HTTPException(404, "Not found")
    groups = await space_service.list_visible_groups_for_user(session, principal, uid)
    out: dict[str, Any] = {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "groups": [
            {"id": str(s.id), "name": s.name, "kind": s.kind.value} for s in groups
        ],
    }
    if principal.is_admin:
        out["providers"] = providers_for(user)
        out["created_at"] = user.created_at.isoformat() if user.created_at else None
        deletable, reason = await user_delete_eligibility(
            session, actor_user_id=principal.user_id, target=user
        )
        out["deletable"] = deletable
        out["delete_blocked_reason"] = reason
    return out


@api_router.delete("/users/{user_id}")
async def remove_user(
    user_id: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, str]:
    """Admin hard-delete of a directory user (IdP-bound roster members refused)."""
    if not principal.is_admin:
        raise HTTPException(403, "Admin required")
    try:
        uid = uuid.UUID(user_id)
    except ValueError as exc:
        raise HTTPException(404, "Not found") from exc
    try:
        await delete_user(
            session, actor_user_id=principal.user_id, target_user_id=uid
        )
    except LookupError as exc:
        raise HTTPException(404, "Not found") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"status": "ok"}


@api_router.get("/spaces")
async def spaces(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> list[dict]:
    rows = await space_service.list_spaces(session, principal)
    return [{"id": str(s.id), "name": s.name, "kind": s.kind.value} for s in rows]


@api_router.post("/spaces")
async def create_space(
    body: GroupIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    space = await space_service.create_group(session, principal, name=body.name)
    return {"id": str(space.id), "name": space.name, "kind": space.kind.value}


@api_router.get("/spaces/{space_id}")
async def show_space(
    space_id: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    space = await space_service.show_space(session, principal, uuid.UUID(space_id))
    if not space:
        raise HTTPException(404, "Not found")
    return {
        "id": str(space.id),
        "name": space.name,
        "kind": space.kind.value,
        "created_by": str(space.created_by_user_id) if space.created_by_user_id else None,
        "members": [
            {
                "user_id": str(m.user_id),
                "email": m.user.email if m.user else None,
                "display_name": m.user.display_name if m.user else None,
            }
            for m in space.members
        ],
    }


@api_router.patch("/spaces/{space_id}")
async def rename_space(
    space_id: str,
    body: GroupIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    try:
        space = await space_service.rename_space(
            session, principal, uuid.UUID(space_id), body.name
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"id": str(space.id), "name": space.name, "kind": space.kind.value}


@api_router.delete("/spaces/{space_id}")
async def delete_space(
    space_id: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    try:
        await space_service.delete_space(session, principal, uuid.UUID(space_id))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"status": "ok"}


@api_router.post("/spaces/{space_id}/members")
async def add_member(
    space_id: str,
    body: MemberIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    sid = uuid.UUID(space_id)
    await space_service.require_roster_manager(session, principal, sid)
    if body.user_id:
        target = uuid.UUID(body.user_id)
    elif body.email:
        user = await get_user_by_email(session, body.email)
        if not user:
            raise HTTPException(404, "No user with that email")
        target = user.id
    else:
        raise HTTPException(400, "user_id or email required")
    await space_service.add_member(session, principal, uuid.UUID(space_id), target)
    return {"status": "ok", "user_id": str(target)}


@api_router.delete("/spaces/{space_id}/members/{user_id}")
async def remove_member(
    space_id: str,
    user_id: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    try:
        await space_service.remove_member(
            session, principal, uuid.UUID(space_id), uuid.UUID(user_id)
        )
    except (PermissionError, ValueError) as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"status": "ok"}


def _parse_types(raw: str | None) -> list[str] | None:
    """Split a comma-separated ``types`` query into tokens."""
    if raw is None:
        return None
    return [part.strip() for part in raw.split(",") if part.strip()]


@api_router.get("/metrics/memories")
async def memory_metrics(
    range: str | None = None,
    space_id: str | None = None,
    space_scope: str | None = None,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """ACL-scoped corpus aggregates for the Metrics page."""
    try:
        return await metrics_service.memory_metrics(
            session,
            principal,
            range_key=range,
            space_id=_uid(space_id),
            space_scope=space_scope,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@api_router.get("/memories")
async def browse_memories(
    space_id: str | None = None,
    space_scope: str | None = None,
    kind: str | None = None,
    type: str | None = None,
    types: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    include_expired: bool = False,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> list[dict]:
    try:
        rows = await memory_service.browse(
            session,
            principal,
            space_id=_uid(space_id),
            space_scope=space_scope,
            kind=kind,
            entry_type=type,
            types=_parse_types(types),
            limit=limit,
            offset=offset,
            include_expired=include_expired,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return await _memories_out(session, rows)


@api_router.get("/memories/search")
async def search_memories(
    q: str,
    space_id: str | None = None,
    space_scope: str | None = None,
    type: str | None = None,
    types: str | None = None,
    kind: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    include_expired: bool = False,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> list[dict]:
    try:
        digests = await memory_service.search(
            session,
            principal,
            query=q,
            space_id=_uid(space_id),
            space_scope=space_scope,
            kind=kind,
            entry_type=type,
            types=_parse_types(types),
            limit=limit,
            offset=offset,
            include_expired=include_expired,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return await _digests_out(session, digests)


@api_router.post("/memories")
async def create_memory(
    body: RememberIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    try:
        memory = await memory_service.remember(
            session,
            principal,
            title=body.title,
            body=body.body,
            entry_type=body.type,
            tags=body.tags,
            space_id=_uid(body.space_id),
            group=body.group,
            visibility=body.visibility,
            expires_at=_dt(body.expires_at),
            ttl_days=body.ttl_days,
            permanent=body.permanent,
            path_hint=body.path_hint,
            repo_hint=body.repo_hint,
            force=body.force,
        )
    except SpaceResolveError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return await _memory_out(session, memory, full=True)


@api_router.get("/memories/{memory_id}")
async def get_memory(
    memory_id: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    memory = await memory_service.open_memory(
        session, principal, memory_id=uuid.UUID(memory_id), recall=True
    )
    if not memory:
        raise HTTPException(404, "Not found")
    return await _memory_out(session, memory, full=True)


@api_router.patch("/memories/{memory_id}")
async def patch_memory(
    memory_id: str,
    body: ReviseIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    try:
        memory = await memory_service.revise(
            session,
            principal,
            uuid.UUID(memory_id),
            title=body.title,
            body=body.body,
            entry_type=body.type,
            tags=body.tags,
            expires_at=_dt(body.expires_at),
            clear_expiry=body.clear_expiry,
            path_hint=body.path_hint,
            repo_hint=body.repo_hint,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return await _memory_out(session, memory, full=True)


@api_router.delete("/memories/{memory_id}")
async def delete_memory(
    memory_id: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    try:
        await memory_service.forget(session, principal, uuid.UUID(memory_id))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"status": "deleted"}


@api_router.post("/guides")
async def create_guide(
    body: GuideIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    try:
        memory = await memory_service.guide_write(
            session,
            principal,
            title=body.title,
            body=body.body,
            tags=body.tags,
            space_id=_uid(body.space_id),
            group=body.group,
            visibility=body.visibility,
            expires_at=_dt(body.expires_at),
            ttl_days=body.ttl_days,
            permanent=body.permanent,
        )
    except SpaceResolveError as exc:
        raise HTTPException(422, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return await _memory_out(session, memory, full=True)


@api_router.get("/api-keys")
async def list_keys(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> list[dict]:
    """List active keys: own for users, all accounts for admins."""
    try:
        keys = await api_key_service.list_api_keys(session, principal)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return [
        {
            "id": str(k.id),
            "name": k.name,
            "prefix": k.key_prefix,
            "created_at": k.created_at.isoformat() if k.created_at else None,
            "user_id": str(k.user_id),
        }
        for k in keys
    ]


@api_router.post("/api-keys")
async def create_key(
    body: ApiKeyIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    try:
        key, raw = await api_key_service.create_api_key(
            session, principal, name=body.name, user_id=_uid(body.user_id)
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {
        "id": str(key.id),
        "name": key.name,
        "prefix": key.key_prefix,
        "key": raw,
        "note": "Copy now; the secret is not shown again.",
    }


@api_router.post("/api-keys/cycle")
async def cycle_key(
    body: ApiKeyCycleIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Revoke the target user's active keys and issue one new random secret."""
    try:
        key, raw = await api_key_service.cycle_api_key(
            session, principal, user_id=_uid(body.user_id), name=body.name
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {
        "id": str(key.id),
        "name": key.name,
        "prefix": key.key_prefix,
        "user_id": str(key.user_id),
        "key": raw,
        "note": "Copy now; the secret is not shown again. Prior keys for this user are revoked.",
    }


@api_router.delete("/api-keys/{key_id}")
async def revoke_key(
    key_id: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    try:
        await api_key_service.revoke_api_key(session, principal, uuid.UUID(key_id))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"status": "revoked"}


class AuthGroupMemberIn(BaseModel):
    user_id: str


class AuthGroupIdpBindIn(BaseModel):
    idp_group_id: str | None = None
    idp_group_name: str | None = None


class RegistrationSettingIn(BaseModel):
    enabled: bool


@api_router.get("/system/registration")
async def get_registration_setting(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, bool]:
    """Admin: whether public self-registration is enabled."""
    if not principal.is_admin:
        raise HTTPException(403, "Administrators only")
    return {"enabled": await instance_settings_service.get_register_enabled(session)}


@api_router.put("/system/registration")
async def put_registration_setting(
    body: RegistrationSettingIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, bool]:
    """Admin: enable or disable public self-registration."""
    try:
        enabled = await instance_settings_service.set_register_enabled(
            session, principal, body.enabled
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"enabled": enabled}


@api_router.get("/auth-groups")
async def list_auth_groups(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> list[dict[str, Any]]:
    try:
        return await auth_group_service.list_groups(session, principal)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@api_router.get("/auth-groups/idp-groups")
async def list_auth_idp_groups(
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Admin: list IdP directory groups for the Bound IdP group dropdown."""
    if not principal.is_admin:
        raise HTTPException(403, "Administrators only")
    from memchentic.auth.idp_groups import list_idp_groups

    result = await list_idp_groups(get_settings())
    return {
        "provider": result.provider,
        "supports_groups": result.supports_groups,
        "groups": [{"id": g.id, "name": g.name} for g in result.groups],
        "error": result.error,
    }


@api_router.get("/auth-groups/{slug}/idp-bind")
async def get_auth_group_idp_bind(
    slug: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    try:
        return await auth_group_service.get_idp_bind(session, principal, slug)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@api_router.put("/auth-groups/{slug}/idp-bind")
async def put_auth_group_idp_bind(
    slug: str,
    body: AuthGroupIdpBindIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    try:
        return await auth_group_service.set_idp_bind(
            session,
            principal,
            slug,
            idp_group_id=body.idp_group_id,
            idp_group_name=body.idp_group_name,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@api_router.get("/auth-groups/{slug}/members")
async def list_auth_group_members(
    slug: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> list[dict[str, Any]]:
    try:
        return await auth_group_service.list_members(session, principal, slug)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@api_router.post("/auth-groups/{slug}/members")
async def add_auth_group_member(
    slug: str,
    body: AuthGroupMemberIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, Any]:
    try:
        return await auth_group_service.add_member(
            session, principal, slug, uuid.UUID(body.user_id)
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@api_router.delete("/auth-groups/{slug}/members/{user_id}")
async def remove_auth_group_member(
    slug: str,
    user_id: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict[str, str]:
    try:
        await auth_group_service.remove_member(
            session, principal, slug, uuid.UUID(user_id)
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"status": "removed"}
