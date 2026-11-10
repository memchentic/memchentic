"""Memory and guide routes: browse, search, CRUD and metrics."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.api.schemas import GuideIn, RememberIn, ReviseIn
from memchentic.auth.principal import Principal
from memchentic.deps import db_session, get_principal
from memchentic.services import guides as guide_service
from memchentic.services import limits, memory_serialize
from memchentic.services import memories as memory_service
from memchentic.services import metrics as metrics_service
from memchentic.services.validation import parse_datetime, parse_uuid

router = APIRouter()


def _parse_types(raw: str | None) -> list[str] | None:
    """Split a comma-separated ``types`` query into tokens."""
    if raw is None:
        return None
    return [part.strip() for part in raw.split(",") if part.strip()]


@router.get("/metrics/memories")
async def memory_metrics(
    range: str | None = Query(None, max_length=limits.TYPE_TEXT_MAX),
    space_id: str | None = Query(None, max_length=limits.ID_TEXT_MAX),
    space_scope: str | None = Query(None, max_length=limits.TYPE_TEXT_MAX),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """ACL-scoped corpus aggregates for the Metrics page."""
    return await metrics_service.memory_metrics(
        session,
        principal,
        range_key=range,
        space_id=parse_uuid(space_id, "space_id"),
        space_scope=space_scope,
    )


@router.get("/memories")
async def browse_memories(
    space_id: str | None = Query(None, max_length=limits.ID_TEXT_MAX),
    space_scope: str | None = Query(None, max_length=limits.TYPE_TEXT_MAX),
    kind: str | None = Query(None, max_length=limits.TYPE_TEXT_MAX),
    type: str | None = Query(None, max_length=limits.TYPE_TEXT_MAX),
    types: str | None = Query(None, max_length=limits.TYPES_QUERY_MAX),
    limit: int | None = None,
    offset: int = Query(0, ge=0, le=limits.OFFSET_MAX),
    include_expired: bool = False,
    tags: str | None = Query(None, max_length=limits.QUERY_MAX),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> list[dict]:
    """List entries and guides in the spaces the caller can read, newest first.

    Authenticated callers. Errors: 400 or 422 on invalid filters.
    """
    rows = await memory_service.browse(
        session,
        principal,
        space_id=parse_uuid(space_id, "space_id"),
        space_scope=space_scope,
        kind=kind,
        entry_type=type,
        types=_parse_types(types),
        limit=limit,
        offset=offset,
        include_expired=include_expired,
        tags=_parse_types(tags),
    )
    return await memory_serialize.memories_out(session, rows)


@router.get("/memories/search")
async def search_memories(
    q: str = Query(..., max_length=limits.QUERY_MAX),
    space_id: str | None = Query(None, max_length=limits.ID_TEXT_MAX),
    space_scope: str | None = Query(None, max_length=limits.TYPE_TEXT_MAX),
    type: str | None = Query(None, max_length=limits.TYPE_TEXT_MAX),
    types: str | None = Query(None, max_length=limits.TYPES_QUERY_MAX),
    kind: str | None = Query(None, max_length=limits.TYPE_TEXT_MAX),
    limit: int | None = None,
    offset: int = Query(0, ge=0, le=limits.OFFSET_MAX),
    include_expired: bool = False,
    touch: bool = True,
    tags: str | None = Query(None, max_length=limits.QUERY_MAX),
    path_hint: str | None = Query(None, max_length=limits.PATH_HINT_MAX),
    repo_hint: str | None = Query(None, max_length=limits.REPO_HINT_MAX),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> list[dict]:
    """Hybrid search over the spaces the caller can read.

    Authenticated callers. Without a type filter only entries are searched.
    Errors: 400 or 422 on invalid filters.
    """
    digests = await memory_service.search(
        session,
        principal,
        query=q,
        space_id=parse_uuid(space_id, "space_id"),
        space_scope=space_scope,
        kind=kind,
        entry_type=type,
        types=_parse_types(types),
        limit=limit,
        offset=offset,
        include_expired=include_expired,
        touch=touch,
        tags=_parse_types(tags),
        path_hint=path_hint,
        repo_hint=repo_hint,
    )
    return await memory_serialize.digests_out(session, digests)


@router.post("/memories")
async def create_memory(
    body: RememberIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Create an entry in a space the caller can write to.

    Authenticated callers. Errors: 403 when the space is not writable,
    4xx on a refused near-duplicate or invalid input.
    """
    memory = await memory_service.remember(
        session,
        principal,
        title=body.title,
        body=body.body,
        entry_type=body.type,
        tags=body.tags,
        space_id=parse_uuid(body.space_id, "space_id"),
        group=body.group,
        visibility=body.visibility,
        expires_at=parse_datetime(body.expires_at, "expires_at"),
        ttl_days=body.ttl_days,
        permanent=body.permanent,
        path_hint=body.path_hint,
        repo_hint=body.repo_hint,
        force=body.force,
    )
    return await memory_serialize.memory_out(session, memory, full=True)


@router.get("/memories/{memory_id}")
async def get_memory(
    memory_id: uuid.UUID,
    touch: bool = False,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Return one memory in full.

    Viewing a memory is not a recall: by default the sliding TTL and the usage
    counters are left alone, so the GUI detail page can be opened freely. A
    REST client that reads a memory in order to use it passes ``?touch=true``
    to get the same renewal as the MCP ``open`` tool.
    """
    memory = await memory_service.open_memory(session, principal, memory_id=memory_id, recall=touch)
    if not memory:
        raise HTTPException(404, "Not found")
    return await memory_serialize.memory_out(session, memory, full=True)


@router.patch("/memories/{memory_id}")
async def patch_memory(
    memory_id: uuid.UUID,
    body: ReviseIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Patch an entry or guide. Its creator, the group creator, or an administrator.

    Errors: 403 when not allowed, 404 when it does not exist, 422 on invalid input.
    """
    memory = await memory_service.revise(
        session,
        principal,
        memory_id,
        title=body.title,
        body=body.body,
        entry_type=body.type,
        tags=body.tags,
        expires_at=parse_datetime(body.expires_at, "expires_at"),
        clear_expiry=body.clear_expiry,
        ttl_days=body.ttl_days,
        permanent=body.permanent,
        path_hint=body.path_hint,
        repo_hint=body.repo_hint,
    )
    return await memory_serialize.memory_out(session, memory, full=True)


@router.delete("/memories/{memory_id}")
async def delete_memory(
    memory_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Delete an entry or guide. Its creator, the group creator, or an administrator.

    Errors: 403 when not allowed, 404 when it does not exist.
    """
    await memory_service.forget(session, principal, memory_id)
    return {"status": "deleted", "id": str(memory_id)}


@router.post("/guides")
async def create_guide(
    body: GuideIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Write a guide; a near-duplicate in the same space is updated instead.

    Authenticated callers. Errors: 403 when the space is not writable,
    422 on invalid input.
    """
    memory = await guide_service.guide_write(
        session,
        principal,
        title=body.title,
        body=body.body,
        tags=body.tags,
        space_id=parse_uuid(body.space_id, "space_id"),
        group=body.group,
        visibility=body.visibility,
        expires_at=parse_datetime(body.expires_at, "expires_at"),
        ttl_days=body.ttl_days,
        permanent=body.permanent,
    )
    return await memory_serialize.memory_out(session, memory, full=True)
