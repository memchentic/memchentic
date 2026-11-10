from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastmcp import FastMCP

from memchentic.mcp.context import require_principal, require_session
from memchentic.models.entities import User
from memchentic.services import memories as memory_service
from memchentic.services import spaces as space_service
from memchentic.services.spaces import Visibility

mcp = FastMCP(name="memchentic")


def _parse_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    return uuid.UUID(value)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


@mcp.tool
async def me() -> dict[str, Any]:
    """Who am I (subject, provider, personal space, memberships)?"""
    principal = require_principal()
    session = require_session()
    spaces = await space_service.list_spaces(session, principal)
    personal = (
        await space_service.get_personal_space(session, principal.user_id)
        if principal.user_id
        else None
    )
    user = await session.get(User, principal.user_id) if principal.user_id else None
    default_shared = user.default_shared_space_id if user else None
    return {
        "id": str(principal.user_id) if principal.user_id else None,
        "default_shared_space_id": str(default_shared) if default_shared else None,
        "subject": principal.subject,
        "email": principal.email,
        "display_name": principal.display_name,
        "provider": principal.provider,
        "is_admin": principal.is_admin,
        "personal_space_id": str(personal.id) if personal else None,
        "memberships": [{"id": str(s.id), "name": s.name, "kind": s.kind.value} for s in spaces],
    }


@mcp.tool
async def space_list() -> list[dict[str, Any]]:
    """Personal + groups I belong to."""
    principal = require_principal()
    session = require_session()
    spaces = await space_service.list_spaces(session, principal)
    return [{"id": str(s.id), "name": s.name, "kind": s.kind.value} for s in spaces]


@mcp.tool
async def space_create(name: str) -> dict[str, Any]:
    """Create a group; caller becomes owner."""
    principal = require_principal()
    session = require_session()
    space = await space_service.create_group(session, principal, name=name)
    return {"id": str(space.id), "name": space.name, "kind": space.kind.value}


@mcp.tool
async def space_show(space_id: str) -> dict[str, Any]:
    """Detail + members for personal or a group I can see."""
    principal = require_principal()
    session = require_session()
    space = await space_service.show_space(session, principal, uuid.UUID(space_id))
    if not space:
        raise ValueError("Space not found")
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


@mcp.tool
async def remember(
    title: str,
    body: str,
    type: str = "note",
    tags: list[str] | None = None,
    space_id: str | None = None,
    expires_at: str | None = None,
    ttl_days: int | None = None,
    permanent: bool = False,
    path_hint: str | None = None,
    repo_hint: str | None = None,
    force: bool = False,
    group: str | None = None,
    visibility: Visibility | None = None,
) -> dict[str, Any]:
    """Create an entry. Refuses near-duplicates unless force.

    Target: space_id if given; else group (exact name of one of your groups);
    else visibility="shared" (your default shared group, or your only group);
    else personal. Never guesses among several groups.

    Lifetime: omit for the server default (90 days); ttl_days=N for N days
    (7-14 scratch, 180 quarterly); permanent=true or ttl_days=0 never expires.
    Each recall (find hit / open) renews a live memory's window.
    """
    principal = require_principal()
    session = require_session()
    memory = await memory_service.remember(
        session,
        principal,
        title=title,
        body=body,
        entry_type=type,
        tags=tags,
        space_id=_parse_uuid(space_id),
        group=group,
        visibility=visibility,
        expires_at=_parse_dt(expires_at),
        ttl_days=ttl_days,
        permanent=permanent,
        path_hint=path_hint,
        repo_hint=repo_hint,
        force=force,
    )
    return memory_service.memory_to_dict(memory, full=True)


@mcp.tool
async def find(
    query: str,
    type: str | None = None,
    tags: list[str] | None = None,
    space_id: str | None = None,
    path_hint: str | None = None,
    repo_hint: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    include_expired: bool = False,
) -> list[dict[str, Any]]:
    """Hybrid search → digests. Past-due expires_at hidden unless include_expired.

    Default ``limit`` is 20 (max 100). Pass ``offset`` to page beyond the first
    page. Empty/whitespace ``query`` returns no hits.
    """
    principal = require_principal()
    session = require_session()
    digests = await memory_service.find(
        session,
        principal,
        query=query,
        space_id=_parse_uuid(space_id),
        entry_type=type,
        tags=tags,
        path_hint=path_hint,
        repo_hint=repo_hint,
        limit=limit,
        offset=offset,
        include_expired=include_expired,
    )
    return [memory_service.digest_to_dict(d) for d in digests]


@mcp.tool
async def open(
    id: str | None = None, title: str | None = None, space_id: str | None = None
) -> dict[str, Any]:
    """Load full entry by id or title (case-insensitive). Ambiguous titles error."""
    principal = require_principal()
    session = require_session()
    memory = await memory_service.open_memory(
        session,
        principal,
        memory_id=_parse_uuid(id),
        title=title,
        space_id=_parse_uuid(space_id),
        recall=True,
    )
    if not memory:
        raise ValueError("Not found")
    return memory_service.memory_to_dict(memory, full=True)


@mcp.tool
async def revise(
    id: str,
    title: str | None = None,
    body: str | None = None,
    type: str | None = None,
    tags: list[str] | None = None,
    expires_at: str | None = None,
    clear_expiry: bool = False,
    path_hint: str | None = None,
    repo_hint: str | None = None,
) -> dict[str, Any]:
    """Patch title/body/type/tags/expiry."""
    principal = require_principal()
    session = require_session()
    memory = await memory_service.revise(
        session,
        principal,
        uuid.UUID(id),
        title=title,
        body=body,
        entry_type=type,
        tags=tags,
        expires_at=_parse_dt(expires_at),
        clear_expiry=clear_expiry,
        path_hint=path_hint,
        repo_hint=repo_hint,
    )
    return memory_service.memory_to_dict(memory, full=True)


@mcp.tool
async def forget(id: str) -> dict[str, str]:
    """Delete an entry I can write."""
    principal = require_principal()
    session = require_session()
    await memory_service.forget(session, principal, uuid.UUID(id))
    return {"status": "deleted", "id": id}


@mcp.tool
async def browse(
    space_id: str | None = None,
    kind: str | None = None,
    type: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    include_expired: bool = False,
) -> list[dict[str, Any]]:
    """Paginated listing without a query."""
    principal = require_principal()
    session = require_session()
    rows = await memory_service.browse(
        session,
        principal,
        space_id=_parse_uuid(space_id),
        kind=kind,
        entry_type=type,
        limit=limit,
        offset=offset,
        include_expired=include_expired,
    )
    return [memory_service.memory_to_dict(m) for m in rows]


@mcp.tool
async def since(
    since: str | None = None,
    until: str | None = None,
    space_id: str | None = None,
    limit: int | None = None,
    include_expired: bool = False,
) -> list[dict[str, Any]]:
    """Time-window recall → digests."""
    principal = require_principal()
    session = require_session()
    digests = await memory_service.since(
        session,
        principal,
        since_dt=_parse_dt(since),
        until_dt=_parse_dt(until),
        space_id=_parse_uuid(space_id),
        limit=limit,
        include_expired=include_expired,
    )
    return [memory_service.digest_to_dict(d) for d in digests]


@mcp.tool
async def by_tag(
    tags: list[str],
    mode: str = "and",
    space_id: str | None = None,
    limit: int | None = None,
    include_expired: bool = False,
) -> list[dict[str, Any]]:
    """Tag AND/OR → digests."""
    principal = require_principal()
    session = require_session()
    digests = await memory_service.by_tag(
        session,
        principal,
        tags=tags,
        mode=mode,
        space_id=_parse_uuid(space_id),
        limit=limit,
        include_expired=include_expired,
    )
    return [memory_service.digest_to_dict(d) for d in digests]


@mcp.tool
async def relocate(id: str, space_id: str) -> dict[str, Any]:
    """Move an entry to another space I can write."""
    principal = require_principal()
    session = require_session()
    memory = await memory_service.relocate(session, principal, uuid.UUID(id), uuid.UUID(space_id))
    return memory_service.memory_to_dict(memory, full=True)


@mcp.tool
async def guide_write(
    title: str,
    body: str,
    tags: list[str] | None = None,
    space_id: str | None = None,
    group: str | None = None,
    visibility: Visibility | None = None,
    ttl_days: int | None = None,
    permanent: bool = False,
) -> dict[str, Any]:
    """Upsert a guide; duplicate content strengthens confidence and renews it.

    Target resolves like remember: space_id, then group name, then
    visibility="shared", else personal. Lifetime like remember: omit for the
    server default, ttl_days=N, or permanent=true.
    """
    principal = require_principal()
    session = require_session()
    memory = await memory_service.guide_write(
        session,
        principal,
        title=title,
        body=body,
        tags=tags,
        space_id=_parse_uuid(space_id),
        group=group,
        visibility=visibility,
        ttl_days=ttl_days,
        permanent=permanent,
    )
    return memory_service.memory_to_dict(memory, full=True)


@mcp.tool
async def guide_find(
    query: str,
    space_id: str | None = None,
    limit: int | None = None,
    include_expired: bool = False,
) -> list[dict[str, Any]]:
    """Search guides by query; rank by confidence × recency."""
    principal = require_principal()
    session = require_session()
    digests = await memory_service.guide_find(
        session,
        principal,
        query=query,
        space_id=_parse_uuid(space_id),
        limit=limit,
        include_expired=include_expired,
    )
    return [memory_service.digest_to_dict(d) for d in digests]


@mcp.tool
async def about_path(
    path: str,
    repo_hint: str | None = None,
    limit: int | None = None,
    include_expired: bool = False,
) -> list[dict[str, Any]]:
    """Digests tied to a file/dir path (or repo hint)."""
    principal = require_principal()
    session = require_session()
    digests = await memory_service.about_path(
        session,
        principal,
        path=path,
        repo_hint=repo_hint,
        limit=limit,
        include_expired=include_expired,
    )
    return [memory_service.digest_to_dict(d) for d in digests]


@mcp.tool
async def trace(id: str) -> dict[str, Any]:
    """Provenance for an entry/guide."""
    principal = require_principal()
    session = require_session()
    return await memory_service.trace(session, principal, uuid.UUID(id))


@mcp.tool
async def overlap_report(space_id: str | None = None, limit: int | None = None) -> dict[str, Any]:
    """Read-only: near-duplicate pairs, conflicts, soon-to-expire."""
    principal = require_principal()
    session = require_session()
    return await memory_service.overlap_report(
        session, principal, space_id=_parse_uuid(space_id), limit=limit
    )


def create_mcp() -> FastMCP:
    return mcp
