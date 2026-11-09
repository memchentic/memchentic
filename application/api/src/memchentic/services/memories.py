from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import DateTime, Select, and_, case, func, literal, or_, select, update
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
        "recall_count": m.recall_count or 0,
        "last_recalled_at": m.last_recalled_at.isoformat() if m.last_recalled_at else None,
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


def resolve_expiry(
    *,
    expires_at: datetime | None,
    ttl_days: int | None,
    permanent: bool,
    now: datetime | None = None,
) -> datetime | None:
    """Pick the create-time expiry for a new memory.

    Precedence: explicit ``expires_at``, then ``permanent`` / ``ttl_days=0``
    (never expires), then positive ``ttl_days``, then ``MEMORY_DEFAULT_TTL_DAYS``.

    Args:
        expires_at: Absolute expiry supplied by the caller.
        ttl_days: Relative lifetime in days; ``0`` means permanent.
        permanent: Caller wants no expiry.
        now: Reference time; defaults to the current UTC time.

    Returns:
        Expiry timestamp, or None for a permanent memory.

    Raises:
        ValueError: When ``ttl_days`` is negative.
    """
    if expires_at is not None:
        return expires_at
    if ttl_days is not None and ttl_days < 0:
        raise ValueError("ttl_days must be >= 0")
    if permanent or ttl_days == 0:
        return None
    ref = now or datetime.now(UTC)
    days = ttl_days if ttl_days is not None else get_settings().memory_default_ttl_days
    return ref + timedelta(days=days) if days > 0 else None


def slide_expiry(
    current: datetime | None, *, created_at: datetime | None, now: datetime
) -> datetime | None:
    """Push ``current`` out to ``now + MEMORY_TTL_EXTEND_DAYS`` without shortening it.

    The extension is capped at ``created_at + MEMORY_MAX_TTL_DAYS`` when that
    ceiling is enabled. Permanent memories (``None``) stay permanent.

    Args:
        current: Existing expiry.
        created_at: Row creation time for the ceiling.
        now: Reference time.

    Returns:
        New expiry.
    """
    if current is None:
        return None
    settings = get_settings()
    target = now + timedelta(days=settings.memory_ttl_extend_days)
    if settings.memory_max_ttl_days > 0 and created_at is not None:
        target = min(target, created_at + timedelta(days=settings.memory_max_ttl_days))
    return max(current, target)


async def touch_memories(
    session: AsyncSession, principal: Principal, ids: Sequence[str | uuid.UUID]
) -> None:
    """Record a recall of ``ids``: bump usage and slide live expiries.

    At most one touch per memory per ``MEMORY_TTL_TOUCH_DEBOUNCE_HOURS``.
    Past-due memories are counted but not revived. ``updated_at`` is preserved
    so recall does not reset edit recency.

    Args:
        session: Database session.
        principal: Caller (for RLS rebinding on commit).
        ids: Memory ids that were returned or opened.
    """
    uids = [i if isinstance(i, uuid.UUID) else uuid.UUID(str(i)) for i in ids]
    if not uids:
        return
    settings = get_settings()
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=settings.memory_ttl_touch_debounce_hours)
    target: Any = literal(
        now + timedelta(days=settings.memory_ttl_extend_days), DateTime(timezone=True)
    )
    if settings.memory_max_ttl_days > 0:
        target = func.least(
            target, Memory.created_at + timedelta(days=settings.memory_max_ttl_days)
        )
    slid = case(
        (
            and_(Memory.expires_at.is_not(None), Memory.expires_at > now),
            func.greatest(Memory.expires_at, target),
        ),
        else_=Memory.expires_at,
    )
    await session.execute(
        update(Memory)
        .where(
            Memory.id.in_(uids),
            or_(Memory.last_recalled_at.is_(None), Memory.last_recalled_at < cutoff),
        )
        .values(
            recall_count=Memory.recall_count + 1,
            last_recalled_at=now,
            expires_at=slid,
            updated_at=Memory.updated_at,
        )
        .execution_options(synchronize_session=False)
    )
    await commit_with_rls(session, user_id=principal.user_id, is_admin=principal.is_admin)


async def _touch_digests(
    session: AsyncSession, principal: Principal, digests: list[Digest]
) -> list[Digest]:
    await touch_memories(session, principal, [d.id for d in digests])
    return digests


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
        "updated_at": d.updated_at,
        "expires_at": d.expires_at,
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
    """Find an active near-duplicate by embedding similarity or exact title.

    Past-due rows are ignored so expired memories do not block ``remember`` or
    become silent ``guide_write`` upsert targets. Exact title is checked across
    the whole space (not only the nearest-vector window).
    """
    settings = get_settings()
    active = active_memory_clause(include_expired=False)
    wanted = title.strip().lower()

    title_stmt = (
        select(Memory)
        .where(
            Memory.space_id == space_id,
            Memory.kind == kind,
            func.lower(func.trim(Memory.title)) == wanted,
        )
        .order_by(Memory.updated_at.desc())
        .limit(1)
    )
    if active is not None:
        title_stmt = title_stmt.where(active)
    title_hit = (await session.execute(title_stmt)).scalar_one_or_none()
    if title_hit is not None:
        return title_hit

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
    if active is not None:
        stmt = stmt.where(active)
    candidates = (await session.execute(stmt)).all()
    for candidate, dist in candidates:
        # pgvector cosine_distance = 1 - cosine_similarity
        if 1.0 - float(dist) >= settings.near_dup_threshold:
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
    ttl_days: int | None = None,
    permanent: bool = False,
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
    expiry = resolve_expiry(expires_at=expires_at, ttl_days=ttl_days, permanent=permanent)
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
        expires_at=expiry,
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
    recall: bool = False,
) -> Memory | None:
    """Load one memory the caller can see.

    Args:
        session: Database session.
        principal: Authenticated caller.
        memory_id: Memory id (preferred).
        title: Exact title within accessible spaces.
        space_id: Optional space for title lookup.
        recall: Record this read as a recall (usage + sliding TTL).

    Returns:
        Memory, or None when missing or not visible.
    """
    memory: Memory | None = None
    if memory_id:
        result = await session.execute(select(Memory).where(Memory.id == memory_id))
        memory = result.scalar_one_or_none()
        if memory and not await show_space(session, principal, memory.space_id):
            memory = None
    elif title:
        space_ids = await accessible_space_ids(session, principal, space_id)
        wanted = title.strip().lower()
        if not wanted or not space_ids:
            return None
        stmt = select(Memory).where(
            Memory.space_id.in_(space_ids),
            func.lower(func.trim(Memory.title)) == wanted,
        )
        rows = list((await session.execute(stmt)).scalars())
        if len(rows) > 1:
            choices = ", ".join(
                f"{m.id} (space {m.space_id})" for m in rows[:8]
            )
            more = "" if len(rows) <= 8 else f" (+{len(rows) - 8} more)"
            raise ValueError(
                f"Title {title!r} matches {len(rows)} memories; "
                f"pass id or space_id. Matches: {choices}{more}"
            )
        memory = rows[0] if rows else None
    if memory and recall:
        await touch_memories(session, principal, [memory.id])
        await session.refresh(memory)
    return memory


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


def _digest_from_memory(m: Memory, *, score: float = 1.0) -> Digest:
    return Digest(
        id=str(m.id),
        title=m.title,
        score=score,
        snippet=(m.body or "")[:180],
        type=m.entry_type.value if m.entry_type else None,
        tags=list(m.tags or []),
        space_id=str(m.space_id),
        kind=m.kind.value,
        created_by=_creator_id(m),
        updated_at=m.updated_at.isoformat() if m.updated_at else None,
        expires_at=m.expires_at.isoformat() if m.expires_at else None,
    )


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
    digests = [_digest_from_memory(m) for m in rows]
    return await _touch_digests(session, principal, digests)


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
    normalized = (mode or "and").strip().lower()
    if normalized not in {"and", "or"}:
        raise ValueError("by_tag mode must be 'and' or 'or'")
    if normalized == "or":
        stmt = stmt.where(Memory.tags.overlap(tags))
    else:
        stmt = stmt.where(Memory.tags.contains(tags))
    stmt = stmt.order_by(Memory.updated_at.desc()).limit(limit)
    rows = list((await session.execute(stmt)).scalars())
    digests = [_digest_from_memory(m) for m in rows]
    return await _touch_digests(session, principal, digests)


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
    offset: int = 0,
    include_expired: bool = False,
) -> list[Digest]:
    space_ids = await accessible_space_ids(
        session, principal, space_id, space_scope=space_scope
    )
    tokens = resolve_type_tokens(
        types=types, kind="entry" if types is None else None, entry_type=entry_type
    )
    digests = await hybrid_search(
        session,
        query=query,
        space_ids=space_ids,
        type_tokens=tokens,
        tags=tags,
        path_hint=path_hint,
        repo_hint=repo_hint,
        limit=limit,
        offset=offset,
        include_expired=include_expired,
    )
    return await _touch_digests(session, principal, digests)


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
    offset: int = 0,
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
    digests = await hybrid_search(
        session,
        query=query,
        space_ids=space_ids,
        type_tokens=tokens,
        limit=limit,
        offset=offset,
        include_expired=include_expired,
    )
    return await _touch_digests(session, principal, digests)


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
    expires_at: datetime | None = None,
    ttl_days: int | None = None,
    permanent: bool = False,
) -> Memory:
    """Create a guide, or reinforce a near-duplicate one.

    Reinforcement raises confidence, counts as a recall, and slides the expiry
    (an explicit ``expires_at`` / ``ttl_days`` / ``permanent`` replaces it).
    """
    space = await resolve_write_space(
        session, principal, space_id, group=group, visibility=visibility
    )
    explicit_ttl = expires_at is not None or ttl_days is not None or permanent
    dup = await find_near_duplicate(
        session, space_id=space.id, title=title, body=body, kind=MemoryKind.guide
    )
    if dup:
        now = datetime.now(UTC)
        dup.confidence = float(dup.confidence or 1.0) + 1.0
        dup.body = body
        dup.title = title.strip()
        if tags is not None:
            dup.tags = tags
        if explicit_ttl:
            dup.expires_at = resolve_expiry(
                expires_at=expires_at, ttl_days=ttl_days, permanent=permanent, now=now
            )
        elif dup.expires_at is not None and dup.expires_at > now:
            # Never revive a past-due guide via slide; find_near_duplicate skips
            # expired rows, so this branch only extends still-live expiries.
            dup.expires_at = slide_expiry(
                dup.expires_at, created_at=dup.created_at, now=now
            )
        dup.recall_count = (dup.recall_count or 0) + 1
        dup.last_recalled_at = now
        dup.embedding = await get_embedder().aembed_memory_text(dup.title, dup.body)
        dup.updated_at = now
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
        expires_at=resolve_expiry(expires_at=expires_at, ttl_days=ttl_days, permanent=permanent),
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
    digests = await hybrid_search(
        session,
        query=query,
        space_ids=await accessible_space_ids(
            session, principal, space_id, space_scope=space_scope
        ),
        type_tokens=["guide"],
        limit=limit,
        include_expired=include_expired,
    )
    return await _touch_digests(session, principal, digests)


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
    digests = [_digest_from_memory(m) for m in rows]
    return await _touch_digests(session, principal, digests)


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
    expiring_stmt = (
        select(Memory)
        .where(
            Memory.space_id.in_(space_ids),
            Memory.expires_at.is_not(None),
            Memory.expires_at > now,
        )
        .order_by(Memory.expires_at.asc())
        .limit(limit)
    )
    expiring_rows = list((await session.execute(expiring_stmt)).scalars())
    expiring = [
        {
            "id": str(m.id),
            "title": m.title,
            "expires_at": m.expires_at.isoformat() if m.expires_at else None,
        }
        for m in expiring_rows
    ]

    return {
        "near_duplicates": near_duplicates,
        "expiring": expiring,
        "conflicts": [],
        "note": "Read-only hygiene report; merge via revise/forget.",
        "near_dup_threshold": settings.near_dup_threshold,
    }
