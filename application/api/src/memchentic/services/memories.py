from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import Principal
from memchentic.embeddings import get_embedder
from memchentic.models.entities import EntryType, Memory, MemoryKind
from memchentic.search.hybrid import Digest, hybrid_search
from memchentic.services.spaces import Visibility, list_spaces, resolve_write_space, show_space
from memchentic.settings import get_settings


def memory_to_dict(m: Memory, *, full: bool = False) -> dict[str, Any]:
    data = {
        "id": str(m.id),
        "space_id": str(m.space_id),
        "kind": m.kind.value,
        "type": m.entry_type.value if m.entry_type else None,
        "title": m.title,
        "tags": list(m.tags or []),
        "path_hint": m.path_hint,
        "repo_hint": m.repo_hint,
        "expires_at": m.expires_at.isoformat() if m.expires_at else None,
        "confidence": m.confidence,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
        "provenance": m.provenance or {},
    }
    if full:
        data["body"] = m.body
    else:
        body = m.body or ""
        data["snippet"] = body[:180] + ("…" if len(body) > 180 else "")
    return data


def digest_to_dict(d: Digest) -> dict[str, Any]:
    return {
        "id": d.id,
        "title": d.title,
        "score": d.score,
        "snippet": d.snippet,
        "type": d.type,
        "tags": d.tags,
        "space": d.space,
        "kind": d.kind,
    }


async def _accessible_space_ids(
    session: AsyncSession, principal: Principal, space_id: uuid.UUID | None
) -> list[uuid.UUID]:
    if space_id:
        space = await show_space(session, principal, space_id)
        return [space.id] if space else []
    spaces = await list_spaces(session, principal)
    return [s.id for s in spaces]


async def find_near_duplicate(
    session: AsyncSession,
    *,
    space_id: uuid.UUID,
    title: str,
    body: str,
    kind: MemoryKind,
) -> Memory | None:
    settings = get_settings()
    embedder = get_embedder()
    vec = embedder.embed_memory_text(title, body)
    stmt = (
        select(Memory)
        .where(
            Memory.space_id == space_id,
            Memory.kind == kind,
            Memory.embedding.is_not(None),
        )
        .order_by(Memory.embedding.cosine_distance(vec))
        .limit(1)
    )
    result = await session.execute(stmt)
    candidate = result.scalar_one_or_none()
    if not candidate or candidate.embedding is None:
        # title exact match fallback
        result = await session.execute(
            select(Memory).where(
                Memory.space_id == space_id,
                Memory.kind == kind,
                Memory.title == title,
            )
        )
        return result.scalar_one_or_none()

    # cosine_distance = 1 - cosine_similarity for pgvector
    dist_stmt = select(Memory.embedding.cosine_distance(vec)).where(Memory.id == candidate.id)
    dist = (await session.execute(dist_stmt)).scalar_one()
    similarity = 1.0 - float(dist)
    if similarity >= settings.near_dup_threshold:
        return candidate
    if candidate.title.strip().lower() == title.strip().lower():
        return candidate
    return None


async def remember(
    session: AsyncSession,
    principal: Principal,
    *,
    title: str,
    body: str,
    entry_type: str = "note",
    tags: list[str] | None = None,
    space_id: uuid.UUID | None = None,
    expires_at: datetime | None = None,
    path_hint: str | None = None,
    repo_hint: str | None = None,
    force: bool = False,
    provenance: dict | None = None,
    group: str | None = None,
    visibility: Visibility | None = None,
) -> Memory:
    space = await resolve_write_space(
        session, principal, space_id, group=group, visibility=visibility
    )
    et = EntryType(entry_type)
    if not force:
        dup = await find_near_duplicate(
            session, space_id=space.id, title=title, body=body, kind=MemoryKind.entry
        )
        if dup:
            raise ValueError(
                f"Near-duplicate exists ({dup.id}). Pass force=true to create anyway."
            )

    embedder = get_embedder()
    embedding = embedder.embed_memory_text(title, body)
    memory = Memory(
        id=uuid.uuid4(),
        space_id=space.id,
        kind=MemoryKind.entry,
        entry_type=et,
        title=title.strip(),
        body=body,
        tags=tags or [],
        path_hint=path_hint,
        repo_hint=repo_hint,
        expires_at=expires_at,
        embedding=embedding,
        provenance=provenance or {},
        created_by_user_id=principal.user_id,
    )
    session.add(memory)
    await session.commit()
    await session.refresh(memory)
    return memory


async def open_memory(
    session: AsyncSession,
    principal: Principal,
    *,
    memory_id: uuid.UUID | None = None,
    title: str | None = None,
    space_id: uuid.UUID | None = None,
) -> Memory | None:
    if memory_id:
        result = await session.execute(select(Memory).where(Memory.id == memory_id))
        memory = result.scalar_one_or_none()
        if not memory:
            return None
        space = await show_space(session, principal, memory.space_id)
        return memory if space else None

    if title:
        space_ids = await _accessible_space_ids(session, principal, space_id)
        stmt = select(Memory).where(Memory.title == title, Memory.space_id.in_(space_ids))
        result = await session.execute(stmt.limit(1))
        return result.scalar_one_or_none()
    return None


async def revise(
    session: AsyncSession,
    principal: Principal,
    memory_id: uuid.UUID,
    *,
    title: str | None = None,
    body: str | None = None,
    entry_type: str | None = None,
    tags: list[str] | None = None,
    expires_at: datetime | None = None,
    clear_expiry: bool = False,
    path_hint: str | None = None,
    repo_hint: str | None = None,
) -> Memory:
    memory = await open_memory(session, principal, memory_id=memory_id)
    if not memory:
        raise PermissionError("Memory not found")
    text_changed = False
    if title is not None:
        memory.title = title.strip()
        text_changed = True
    if body is not None:
        memory.body = body
        text_changed = True
    if entry_type is not None:
        memory.entry_type = EntryType(entry_type)
    if tags is not None:
        memory.tags = tags
    if clear_expiry:
        memory.expires_at = None
    elif expires_at is not None:
        memory.expires_at = expires_at
    if path_hint is not None:
        memory.path_hint = path_hint
    if repo_hint is not None:
        memory.repo_hint = repo_hint
    if text_changed:
        memory.embedding = get_embedder().embed_memory_text(memory.title, memory.body)
    memory.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(memory)
    return memory


async def forget(
    session: AsyncSession, principal: Principal, memory_id: uuid.UUID
) -> None:
    memory = await open_memory(session, principal, memory_id=memory_id)
    if not memory:
        raise PermissionError("Memory not found")
    await session.delete(memory)
    await session.commit()


async def browse(
    session: AsyncSession,
    principal: Principal,
    *,
    space_id: uuid.UUID | None = None,
    kind: str | None = None,
    entry_type: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[Memory]:
    space_ids = await _accessible_space_ids(session, principal, space_id)
    if not space_ids:
        return []
    limit = get_settings().resolve_browse_limit(limit)
    stmt = select(Memory).where(Memory.space_id.in_(space_ids))
    if kind:
        stmt = stmt.where(Memory.kind == MemoryKind(kind))
    if entry_type:
        stmt = stmt.where(Memory.entry_type == EntryType(entry_type))
    stmt = stmt.order_by(Memory.updated_at.desc()).limit(limit).offset(offset)
    return list((await session.execute(stmt)).scalars())


async def since(
    session: AsyncSession,
    principal: Principal,
    *,
    since_dt: datetime | None = None,
    until_dt: datetime | None = None,
    space_id: uuid.UUID | None = None,
    limit: int | None = None,
) -> list[Digest]:
    space_ids = await _accessible_space_ids(session, principal, space_id)
    if not space_ids:
        return []
    limit = get_settings().resolve_browse_limit(limit)
    stmt = select(Memory).where(Memory.space_id.in_(space_ids))
    if since_dt:
        stmt = stmt.where(Memory.updated_at >= since_dt)
    if until_dt:
        stmt = stmt.where(Memory.updated_at <= until_dt)
    stmt = stmt.order_by(Memory.updated_at.desc()).limit(limit)
    rows = list((await session.execute(stmt)).scalars())
    return [
        Digest(
            id=str(m.id),
            title=m.title,
            score=1.0,
            snippet=(m.body or "")[:180],
            type=m.entry_type.value if m.entry_type else None,
            tags=list(m.tags or []),
            space=str(m.space_id),
            kind=m.kind.value,
        )
        for m in rows
    ]


async def by_tag(
    session: AsyncSession,
    principal: Principal,
    *,
    tags: list[str],
    mode: str = "and",
    space_id: uuid.UUID | None = None,
    limit: int | None = None,
) -> list[Digest]:
    space_ids = await _accessible_space_ids(session, principal, space_id)
    if not space_ids or not tags:
        return []
    limit = get_settings().resolve_browse_limit(limit)
    stmt = select(Memory).where(Memory.space_id.in_(space_ids))
    if mode == "or":
        stmt = stmt.where(Memory.tags.overlap(tags))
    else:
        stmt = stmt.where(Memory.tags.contains(tags))
    stmt = stmt.order_by(Memory.updated_at.desc()).limit(limit)
    rows = list((await session.execute(stmt)).scalars())
    return [
        Digest(
            id=str(m.id),
            title=m.title,
            score=1.0,
            snippet=(m.body or "")[:180],
            type=m.entry_type.value if m.entry_type else None,
            tags=list(m.tags or []),
            space=str(m.space_id),
            kind=m.kind.value,
        )
        for m in rows
    ]


async def relocate(
    session: AsyncSession,
    principal: Principal,
    memory_id: uuid.UUID,
    target_space_id: uuid.UUID,
) -> Memory:
    memory = await open_memory(session, principal, memory_id=memory_id)
    if not memory:
        raise PermissionError("Memory not found")
    target = await resolve_write_space(session, principal, target_space_id)
    memory.space_id = target.id
    memory.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(memory)
    return memory


async def find(
    session: AsyncSession,
    principal: Principal,
    *,
    query: str,
    space_id: uuid.UUID | None = None,
    entry_type: str | None = None,
    tags: list[str] | None = None,
    path_hint: str | None = None,
    repo_hint: str | None = None,
    limit: int | None = None,
) -> list[Digest]:
    space_ids = await _accessible_space_ids(session, principal, space_id)
    return await hybrid_search(
        session,
        query=query,
        space_ids=space_ids,
        kind=MemoryKind.entry,
        entry_type=entry_type,
        tags=tags,
        path_hint=path_hint,
        repo_hint=repo_hint,
        limit=limit,
    )


async def guide_write(
    session: AsyncSession,
    principal: Principal,
    *,
    title: str,
    body: str,
    tags: list[str] | None = None,
    space_id: uuid.UUID | None = None,
    group: str | None = None,
    visibility: Visibility | None = None,
) -> Memory:
    space = await resolve_write_space(
        session, principal, space_id, group=group, visibility=visibility
    )
    dup = await find_near_duplicate(
        session, space_id=space.id, title=title, body=body, kind=MemoryKind.guide
    )
    if dup:
        dup.confidence = float(dup.confidence or 1.0) + 1.0
        dup.body = body
        dup.title = title.strip()
        if tags is not None:
            dup.tags = tags
        dup.embedding = get_embedder().embed_memory_text(dup.title, dup.body)
        dup.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(dup)
        return dup

    memory = Memory(
        id=uuid.uuid4(),
        space_id=space.id,
        kind=MemoryKind.guide,
        entry_type=None,
        title=title.strip(),
        body=body,
        tags=tags or [],
        confidence=1.0,
        embedding=get_embedder().embed_memory_text(title, body),
        created_by_user_id=principal.user_id,
        provenance={},
    )
    session.add(memory)
    await session.commit()
    await session.refresh(memory)
    return memory


async def guide_find(
    session: AsyncSession,
    principal: Principal,
    *,
    query: str,
    space_id: uuid.UUID | None = None,
    limit: int | None = None,
) -> list[Digest]:
    digests = await hybrid_search(
        session,
        query=query,
        space_ids=await _accessible_space_ids(session, principal, space_id),
        kind=MemoryKind.guide,
        limit=limit,
    )
    # Boost by confidence × recency is partly handled by storing confidence;
    # re-rank lightly by loading confidence.
    if not digests:
        return digests
    ids = [uuid.UUID(d.id) for d in digests]
    rows = {
        m.id: m
        for m in (await session.execute(select(Memory).where(Memory.id.in_(ids)))).scalars()
    }
    for d in digests:
        m = rows.get(uuid.UUID(d.id))
        if m:
            d.score = round(d.score * (1.0 + 0.1 * float(m.confidence or 1.0)), 6)
    digests.sort(key=lambda x: x.score, reverse=True)
    return digests


async def about_path(
    session: AsyncSession,
    principal: Principal,
    *,
    path: str,
    repo_hint: str | None = None,
    limit: int | None = None,
) -> list[Digest]:
    space_ids = await _accessible_space_ids(session, principal, None)
    if not space_ids:
        return []
    limit = get_settings().resolve_search_limit(limit)
    stmt = select(Memory).where(
        Memory.space_id.in_(space_ids),
        or_(Memory.path_hint.ilike(f"%{path}%"), Memory.body.ilike(f"%{path}%")),
    )
    if repo_hint:
        stmt = stmt.where(Memory.repo_hint.ilike(f"%{repo_hint}%"))
    stmt = stmt.order_by(Memory.updated_at.desc()).limit(limit)
    rows = list((await session.execute(stmt)).scalars())
    return [
        Digest(
            id=str(m.id),
            title=m.title,
            score=1.0,
            snippet=(m.body or "")[:180],
            type=m.entry_type.value if m.entry_type else None,
            tags=list(m.tags or []),
            space=str(m.space_id),
            kind=m.kind.value,
        )
        for m in rows
    ]


async def trace(
    session: AsyncSession, principal: Principal, memory_id: uuid.UUID
) -> dict[str, Any]:
    memory = await open_memory(session, principal, memory_id=memory_id)
    if not memory:
        raise PermissionError("Memory not found")
    return {
        "id": str(memory.id),
        "title": memory.title,
        "kind": memory.kind.value,
        "created_by": str(memory.created_by_user_id) if memory.created_by_user_id else None,
        "created_at": memory.created_at.isoformat() if memory.created_at else None,
        "provenance": memory.provenance or {},
        "space_id": str(memory.space_id),
    }


async def overlap_report(
    session: AsyncSession,
    principal: Principal,
    *,
    space_id: uuid.UUID | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    limit = settings.resolve_search_limit(limit)
    space_ids = await _accessible_space_ids(session, principal, space_id)
    if not space_ids:
        return {"near_duplicates": [], "expiring": [], "conflicts": []}

    rows = list(
        (
            await session.execute(
                select(Memory)
                .where(Memory.space_id.in_(space_ids), Memory.embedding.is_not(None))
                .order_by(Memory.updated_at.desc())
                .limit(200)
            )
        ).scalars()
    )

    near_duplicates = []
    seen: set[tuple[str, str]] = set()
    for i, a in enumerate(rows):
        for b in rows[i + 1 :]:
            if a.space_id != b.space_id or a.kind != b.kind:
                continue
            # cheap title similarity + distance query
            if a.title.strip().lower() == b.title.strip().lower():
                key = tuple(sorted((str(a.id), str(b.id))))
                if key not in seen:
                    seen.add(key)
                    near_duplicates.append(
                        {
                            "a": str(a.id),
                            "b": str(b.id),
                            "title": a.title,
                            "reason": "same_title",
                        }
                    )
            if len(near_duplicates) >= limit:
                break
        if len(near_duplicates) >= limit:
            break

    now = datetime.now(UTC)
    expiring = [
        {
            "id": str(m.id),
            "title": m.title,
            "expires_at": m.expires_at.isoformat() if m.expires_at else None,
        }
        for m in rows
        if m.expires_at and m.expires_at > now
    ][:limit]
    # soonest first
    expiring.sort(key=lambda x: x["expires_at"] or "")

    return {
        "near_duplicates": near_duplicates,
        "expiring": expiring,
        "conflicts": [],
        "note": "Read-only hygiene report; merge via revise/forget.",
        "near_dup_threshold": settings.near_dup_threshold,
    }
