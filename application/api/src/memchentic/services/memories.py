from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import numpy as np
from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import Principal
from memchentic.db.session import commit_with_rls
from memchentic.embeddings import get_embedder
from memchentic.events import memory_event_from_row, publish_memory_event
from memchentic.models.entities import EntryType, Memory, MemoryKind, SpaceKind, User
from memchentic.search.hybrid import (
    Digest,
    active_memory_clause,
    escape_ilike,
    hybrid_search,
    memory_type_clause,
)
from memchentic.services.spaces import (
    Visibility,
    can_manage_group,
    list_spaces,
    resolve_write_space,
    show_space,
)
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
        "created_by": str(m.created_by_user_id) if m.created_by_user_id else None,
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
        "space_id": d.space_id,
        "kind": d.kind,
        "created_by": d.created_by,
    }


async def enrich_creator_fields(
    session: AsyncSession, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Attach ``created_by_email`` / ``created_by_display_name`` from users.

    Args:
        session: DB session.
        rows: Memory or digest dicts that may include ``created_by``.

    Returns:
        The same list, mutated in place with creator label fields.
    """
    raw_ids: set[uuid.UUID] = set()
    for row in rows:
        cid = row.get("created_by")
        if not cid:
            continue
        try:
            raw_ids.add(uuid.UUID(str(cid)))
        except ValueError:
            continue
    by_id: dict[str, User] = {}
    if raw_ids:
        result = await session.execute(select(User).where(User.id.in_(raw_ids)))
        by_id = {str(u.id): u for u in result.scalars().all()}
    for row in rows:
        user = by_id.get(str(row.get("created_by") or ""))
        row["created_by_email"] = user.email if user else None
        row["created_by_display_name"] = user.display_name if user else None
    return rows


def _creator_id(m: Memory) -> str | None:
    return str(m.created_by_user_id) if m.created_by_user_id else None


async def accessible_space_ids(
    session: AsyncSession,
    principal: Principal,
    space_id: uuid.UUID | None,
    *,
    space_scope: str | None = None,
) -> list[uuid.UUID]:
    """Return space ids the principal may read (optionally narrowed).

    Args:
        session: Database session.
        principal: Authenticated caller.
        space_id: When set, return that id only if readable; else all readable.
        space_scope: Optional ``personal`` or ``group`` filter when ``space_id``
            is unset. Ignored when ``space_id`` is set.

    Returns:
        Zero or more space UUIDs.
    """
    if space_id:
        space = await show_space(session, principal, space_id)
        return [space.id] if space else []
    spaces = await list_spaces(session, principal)
    if space_scope:
        kind = SpaceKind(space_scope)
        spaces = [s for s in spaces if s.kind == kind]
    return [s.id for s in spaces]


def resolve_type_tokens(
    *,
    types: Sequence[str] | None = None,
    kind: str | None = None,
    entry_type: str | None = None,
) -> list[str] | None:
    """Normalize type filters to tokens (entry types and/or ``guide``).

    Explicit ``types`` wins. Legacy ``kind`` / ``entry_type`` map to tokens.
    None means no type restriction.
    """
    if types is not None:
        tokens = [t.strip() for t in types if t and t.strip()]
        return tokens or None
    if kind == "guide":
        return ["guide"]
    if kind == "entry":
        return [entry_type] if entry_type else [e.value for e in EntryType]
    if entry_type:
        return [entry_type]
    return None


def _active_select(space_ids: list[uuid.UUID], include_expired: bool) -> Select[tuple[Memory]]:
    """Memories in ``space_ids``, hiding past-due rows unless ``include_expired``."""
    stmt = select(Memory).where(Memory.space_id.in_(space_ids))
    active = active_memory_clause(include_expired=include_expired)
    if active is not None:
        stmt = stmt.where(active)
    return stmt


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
    vec = await embedder.aembed_memory_text(title, body)
    distance = Memory.embedding.cosine_distance(vec)
    stmt = (
        select(Memory, distance)
        .where(
            Memory.space_id == space_id,
            Memory.kind == kind,
            Memory.embedding.is_not(None),
        )
        .order_by(distance)
        .limit(settings.near_dup_candidates)
    )
    candidates = (await session.execute(stmt)).all()
    if not candidates:
        result = await session.execute(
            select(Memory)
            .where(
                Memory.space_id == space_id,
                Memory.kind == kind,
                Memory.title == title,
            )
            .order_by(Memory.updated_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    wanted = title.strip().lower()
    for candidate, dist in candidates:
        # pgvector cosine_distance = 1 - cosine_similarity
        if 1.0 - float(dist) >= settings.near_dup_threshold:
            return candidate
    for candidate, _ in candidates:
        if candidate.title.strip().lower() == wanted:
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
    embedding = await embedder.aembed_memory_text(title, body)
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
    await session.flush()
    await session.refresh(memory)
    await publish_memory_event(session, memory_event_from_row("created", memory))
    await commit_with_rls(
        session, user_id=principal.user_id, is_admin=principal.is_admin
    )
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
        space_ids = await accessible_space_ids(session, principal, space_id)
        stmt = select(Memory).where(Memory.title == title, Memory.space_id.in_(space_ids))
        result = await session.execute(stmt.limit(1))
        return result.scalar_one_or_none()
    return None


async def require_memory_manage(
    session: AsyncSession, principal: Principal, memory: Memory
) -> None:
    """Raise unless the principal may forget/relocate ``memory``.

    Allowed when the caller is an admin, the memory creator, or a manager of
    the memory's group (``can_manage_group``). Member-level revise/guide_write
    stay on ``open_memory`` only.

    Args:
        session: Database session.
        principal: Authenticated caller.
        memory: Already-loaded memory the caller can at least open.

    Raises:
        PermissionError: When the caller may not manage the memory.
    """
    if principal.is_admin:
        return
    if (
        principal.user_id
        and memory.created_by_user_id
        and memory.created_by_user_id == principal.user_id
    ):
        return
    space = await show_space(session, principal, memory.space_id)
    if space is not None and can_manage_group(space, principal):
        return
    raise PermissionError("Not allowed to manage this memory")


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
        memory.embedding = await get_embedder().aembed_memory_text(
            memory.title, memory.body
        )
    memory.updated_at = datetime.now(UTC)
    await publish_memory_event(session, memory_event_from_row("updated", memory))
    await commit_with_rls(
        session, user_id=principal.user_id, is_admin=principal.is_admin
    )
    await session.refresh(memory)
    return memory


async def forget(
    session: AsyncSession, principal: Principal, memory_id: uuid.UUID
) -> None:
    memory = await open_memory(session, principal, memory_id=memory_id)
    if not memory:
        raise PermissionError("Memory not found")
    await require_memory_manage(session, principal, memory)
    event = memory_event_from_row("deleted", memory)
    await session.delete(memory)
    await publish_memory_event(session, event)
    await commit_with_rls(
        session, user_id=principal.user_id, is_admin=principal.is_admin
    )


async def browse(
    session: AsyncSession,
    principal: Principal,
    *,
    space_id: uuid.UUID | None = None,
    space_scope: str | None = None,
    kind: str | None = None,
    entry_type: str | None = None,
    types: Sequence[str] | None = None,
    limit: int | None = None,
    offset: int = 0,
    include_expired: bool = False,
) -> list[Memory]:
    space_ids = await accessible_space_ids(
        session, principal, space_id, space_scope=space_scope
    )
    if not space_ids:
        return []
    limit = get_settings().resolve_browse_limit(limit)
    stmt = _active_select(space_ids, include_expired)
    tokens = resolve_type_tokens(types=types, kind=kind, entry_type=entry_type)
    clause = memory_type_clause(tokens)
    if clause is not None:
        stmt = stmt.where(clause)
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
    include_expired: bool = False,
) -> list[Digest]:
    space_ids = await accessible_space_ids(session, principal, space_id)
    if not space_ids:
        return []
    limit = get_settings().resolve_browse_limit(limit)
    stmt = _active_select(space_ids, include_expired)
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
            space_id=str(m.space_id),
            kind=m.kind.value,
            created_by=_creator_id(m),
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
    include_expired: bool = False,
) -> list[Digest]:
    space_ids = await accessible_space_ids(session, principal, space_id)
    if not space_ids or not tags:
        return []
    limit = get_settings().resolve_browse_limit(limit)
    stmt = _active_select(space_ids, include_expired)
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
            space_id=str(m.space_id),
            kind=m.kind.value,
            created_by=_creator_id(m),
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
    await require_memory_manage(session, principal, memory)
    target = await resolve_write_space(session, principal, target_space_id)
    left = memory_event_from_row("deleted", memory)
    memory.space_id = target.id
    memory.updated_at = datetime.now(UTC)
    await publish_memory_event(session, left)
    await publish_memory_event(session, memory_event_from_row("created", memory))
    await commit_with_rls(
        session, user_id=principal.user_id, is_admin=principal.is_admin
    )
    await session.refresh(memory)
    return memory


async def find(
    session: AsyncSession,
    principal: Principal,
    *,
    query: str,
    space_id: uuid.UUID | None = None,
    space_scope: str | None = None,
    entry_type: str | None = None,
    types: Sequence[str] | None = None,
    tags: list[str] | None = None,
    path_hint: str | None = None,
    repo_hint: str | None = None,
    limit: int | None = None,
    include_expired: bool = False,
) -> list[Digest]:
    space_ids = await accessible_space_ids(
        session, principal, space_id, space_scope=space_scope
    )
    tokens = resolve_type_tokens(
        types=types, kind="entry" if types is None else None, entry_type=entry_type
    )
    return await hybrid_search(
        session,
        query=query,
        space_ids=space_ids,
        type_tokens=tokens,
        tags=tags,
        path_hint=path_hint,
        repo_hint=repo_hint,
        limit=limit,
        include_expired=include_expired,
    )


async def search(
    session: AsyncSession,
    principal: Principal,
    *,
    query: str,
    space_id: uuid.UUID | None = None,
    space_scope: str | None = None,
    kind: str | None = None,
    entry_type: str | None = None,
    types: Sequence[str] | None = None,
    limit: int | None = None,
    include_expired: bool = False,
) -> list[Digest]:
    """Hybrid search across entries and/or guides for the GUI/REST surface."""
    space_ids = await accessible_space_ids(
        session, principal, space_id, space_scope=space_scope
    )
    tokens = resolve_type_tokens(types=types, kind=kind, entry_type=entry_type)
    if tokens is None and kind is None and entry_type is None:
        # Default REST search historically searched entries only.
        tokens = [e.value for e in EntryType]
    return await hybrid_search(
        session,
        query=query,
        space_ids=space_ids,
        type_tokens=tokens,
        limit=limit,
        include_expired=include_expired,
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
        dup.embedding = await get_embedder().aembed_memory_text(dup.title, dup.body)
        dup.updated_at = datetime.now(UTC)
        await publish_memory_event(session, memory_event_from_row("updated", dup))
        await commit_with_rls(
            session, user_id=principal.user_id, is_admin=principal.is_admin
        )
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
        embedding=await get_embedder().aembed_memory_text(title, body),
        created_by_user_id=principal.user_id,
        provenance={},
    )
    session.add(memory)
    await session.flush()
    await session.refresh(memory)
    await publish_memory_event(session, memory_event_from_row("created", memory))
    await commit_with_rls(
        session, user_id=principal.user_id, is_admin=principal.is_admin
    )
    await session.refresh(memory)
    return memory


async def guide_find(
    session: AsyncSession,
    principal: Principal,
    *,
    query: str,
    space_id: uuid.UUID | None = None,
    space_scope: str | None = None,
    limit: int | None = None,
    include_expired: bool = False,
) -> list[Digest]:
    return await hybrid_search(
        session,
        query=query,
        space_ids=await accessible_space_ids(
            session, principal, space_id, space_scope=space_scope
        ),
        type_tokens=["guide"],
        limit=limit,
        include_expired=include_expired,
    )


async def about_path(
    session: AsyncSession,
    principal: Principal,
    *,
    path: str,
    repo_hint: str | None = None,
    limit: int | None = None,
    include_expired: bool = False,
) -> list[Digest]:
    space_ids = await accessible_space_ids(session, principal, None)
    if not space_ids:
        return []
    limit = get_settings().resolve_search_limit(limit)
    path_pat = f"%{escape_ilike(path)}%"
    stmt = _active_select(space_ids, include_expired).where(
        or_(
            Memory.path_hint.ilike(path_pat, escape="\\"),
            Memory.body.ilike(path_pat, escape="\\"),
        ),
    )
    if repo_hint:
        stmt = stmt.where(
            Memory.repo_hint.ilike(f"%{escape_ilike(repo_hint)}%", escape="\\")
        )
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
            space_id=str(m.space_id),
            kind=m.kind.value,
            created_by=_creator_id(m),
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
    space_ids = await accessible_space_ids(session, principal, space_id)
    if not space_ids:
        return {"near_duplicates": [], "expiring": [], "conflicts": []}

    rows = list(
        (
            await session.execute(
                select(Memory)
                .where(Memory.space_id.in_(space_ids), Memory.embedding.is_not(None))
                .order_by(Memory.updated_at.desc())
                .limit(settings.overlap_scan_limit)
            )
        ).scalars()
    )

    near_duplicates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def _add(a: Memory, b: Memory, reason: str, similarity: float | None = None) -> None:
        key = tuple(sorted((str(a.id), str(b.id))))
        if key in seen:
            return
        seen.add(key)
        item: dict[str, Any] = {
            "a": str(a.id),
            "b": str(b.id),
            "title": a.title,
            "reason": reason,
        }
        if similarity is not None:
            item["similarity"] = round(similarity, 4)
        near_duplicates.append(item)

    by_title: dict[tuple[uuid.UUID, MemoryKind, str], Memory] = {}
    for m in rows:
        key = (m.space_id, m.kind, m.title.strip().lower())
        first = by_title.setdefault(key, m)
        if first is not m:
            _add(first, m, "same_title")
        if len(near_duplicates) >= limit:
            break

    if len(near_duplicates) < limit:
        groups: dict[tuple[uuid.UUID, MemoryKind], list[Memory]] = {}
        for m in rows:
            groups.setdefault((m.space_id, m.kind), []).append(m)
        threshold = settings.near_dup_threshold
        for members in groups.values():
            if len(members) < 2 or len(near_duplicates) >= limit:
                continue
            matrix = np.asarray([m.embedding for m in members], dtype=np.float32)
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            unit = matrix / norms
            sims = unit @ unit.T
            idx_a, idx_b = np.where(np.triu(sims, k=1) >= threshold)
            for i, j in zip(idx_a.tolist(), idx_b.tolist(), strict=True):
                _add(members[i], members[j], "near_dup", float(sims[i, j]))
                if len(near_duplicates) >= limit:
                    break
    near_duplicates = near_duplicates[:limit]

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
