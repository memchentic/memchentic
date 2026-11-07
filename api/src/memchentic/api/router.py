from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import Principal, check_admin_password
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
    get_user_by_email,
    list_users,
    register_internal_user,
)
from memchentic.deps import db_session, get_principal
from memchentic.models.entities import User
from memchentic.services import api_keys as api_key_service
from memchentic.services import memories as memory_service
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
    team: str | None = None
    visibility: Visibility | None = None
    expires_at: str | None = None
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


class TeamIn(BaseModel):
    name: str


class MemberIn(BaseModel):
    user_id: str | None = None
    email: str | None = None


class ApiKeyIn(BaseModel):
    name: str = "default"
    user_id: str | None = None


class GuideIn(BaseModel):
    title: str
    body: str
    tags: list[str] = []
    space_id: str | None = None
    team: str | None = None
    visibility: Visibility | None = None


def _uid(value: str | None) -> uuid.UUID | None:
    return uuid.UUID(value) if value else None


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


@api_router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "memchentic"}


@api_router.post("/auth/register")
async def register(body: RegisterIn, session: AsyncSession = Depends(db_session)) -> dict:
    settings = get_settings()
    if settings.oauth_provider != "internal":
        raise HTTPException(400, "Registration only in internal mode")
    try:
        user = await register_internal_user(
            session, email=body.email, password=body.password, display_name=body.display_name
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
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
    body: LoginIn, response: Response, session: AsyncSession = Depends(db_session)
) -> dict:
    settings = get_settings()
    # admin break-glass
    if check_admin_password(body.email, body.password, settings):
        subject, email, provider, user_id = (
            "admin",
            settings.admin_contact_email,
            "admin",
            None,
        )
        extra: dict[str, Any] = {"is_admin": True, "email": email}
    else:
        user = await authenticate_internal(session, email=body.email, password=body.password)
        if not user:
            raise HTTPException(401, "Invalid credentials")
        subject, email, provider, user_id = str(user.id), user.email, user.provider, user.id
        extra = {
            "is_admin": False,
            "id": str(user.id),
            "email": user.email,
            "display_name": user.display_name,
        }

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
    personal = next((s for s in spaces if s.kind.value == "personal"), None)
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
    """Authenticated directory for team invites; admins get extra metadata."""
    rows = await list_users(session)
    out: list[dict] = []
    for u in rows:
        row: dict = {
            "id": str(u.id),
            "email": u.email,
            "display_name": u.display_name,
        }
        if principal.is_admin:
            row["provider"] = u.provider
            row["created_at"] = u.created_at.isoformat() if u.created_at else None
        out.append(row)
    return out


@api_router.get("/spaces")
async def spaces(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> list[dict]:
    rows = await space_service.list_spaces(session, principal)
    return [{"id": str(s.id), "name": s.name, "kind": s.kind.value} for s in rows]


@api_router.post("/spaces")
async def create_space(
    body: TeamIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    space = await space_service.create_team(session, principal, name=body.name)
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
    body: TeamIn,
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


@api_router.post("/spaces/{space_id}/archive")
async def archive_space(
    space_id: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    try:
        space = await space_service.archive_space(session, principal, uuid.UUID(space_id))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {"id": str(space.id), "archived_at": space.archived_at.isoformat() if space.archived_at else None}


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


@api_router.get("/memories")
async def browse_memories(
    space_id: str | None = None,
    kind: str | None = None,
    type: str | None = None,
    limit: int = 50,
    offset: int = 0,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> list[dict]:
    rows = await memory_service.browse(
        session,
        principal,
        space_id=_uid(space_id),
        kind=kind,
        entry_type=type,
        limit=limit,
        offset=offset,
    )
    return [memory_service.memory_to_dict(m) for m in rows]


@api_router.get("/memories/search")
async def search_memories(
    q: str,
    space_id: str | None = None,
    type: str | None = None,
    kind: str = "entry",
    limit: int = 20,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> list[dict]:
    if kind == "guide":
        digests = await memory_service.guide_find(
            session, principal, query=q, space_id=_uid(space_id), limit=limit
        )
    else:
        digests = await memory_service.find(
            session, principal, query=q, space_id=_uid(space_id), entry_type=type, limit=limit
        )
    return [memory_service.digest_to_dict(d) for d in digests]


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
            team=body.team,
            visibility=body.visibility,
            expires_at=_dt(body.expires_at),
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
    return memory_service.memory_to_dict(memory, full=True)


@api_router.get("/memories/{memory_id}")
async def get_memory(
    memory_id: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    memory = await memory_service.open_memory(
        session, principal, memory_id=uuid.UUID(memory_id)
    )
    if not memory:
        raise HTTPException(404, "Not found")
    return memory_service.memory_to_dict(memory, full=True)


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
    return memory_service.memory_to_dict(memory, full=True)


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
            team=body.team,
            visibility=body.visibility,
        )
    except SpaceResolveError as exc:
        raise HTTPException(422, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return memory_service.memory_to_dict(memory, full=True)


@api_router.get("/api-keys")
async def list_keys(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> list[dict]:
    keys = await api_key_service.list_api_keys(session, principal)
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
    key, raw = await api_key_service.create_api_key(
        session, principal, name=body.name, user_id=_uid(body.user_id)
    )
    return {
        "id": str(key.id),
        "name": key.name,
        "prefix": key.key_prefix,
        "key": raw,
        "note": "Copy now; the secret is not shown again.",
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
