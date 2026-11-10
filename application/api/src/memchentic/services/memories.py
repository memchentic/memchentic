"""Entry and guide services shared by the REST API and the MCP tools."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import (
    CursorResult,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import Principal
from memchentic.db.session import advisory_xact_lock, commit_with_rls, set_rls_context
from memchentic.embeddings import get_embedder
from memchentic.events import memory_event_from_row, publish_memory_event
from memchentic.events.memory_bus import Action
from memchentic.models.entities import EntryType, Memory, MemoryKind
from memchentic.search.hybrid import (
    Digest,
    escape_ilike,
    hybrid_search,
    memory_type_clause,
)
from memchentic.services import limits
from memchentic.services.errors import (
    ForbiddenError,
    NearDuplicateError,
    NotFoundError,
    ValidationError,
)
from memchentic.services.memory_hygiene import dedup_lock_key, find_near_duplicate
from memchentic.services.memory_serialize import annotate_rows, digest_from_memory
from memchentic.services.memory_ttl import (
    active_select,
    check_lifetime_args,
    resolve_expiry,
    touch_digests,
    touch_memories,
)
from memchentic.services.spaces import (
    accessible_space_ids,
    can_manage_group,
    get_readable_space,
)
from memchentic.services.validation import (
    check_body,
    check_offset,
    check_optional_text,
    check_query,
    check_tags,
    check_text,
    check_title,
)
from memchentic.services.write_target import Visibility, resolve_write_space
from memchentic.settings import get_settings

# Closed sets shared with the MCP tool signatures (keep in step with the
# ``EntryType`` and ``MemoryKind`` enums; a test asserts they match).
EntryTypeName = Literal[
    "fact", "preference", "decision", "instruction", "feedback", "insight", "note"
]
MemoryKindName = Literal["entry", "guide"]

_KIND_VALUES = frozenset(k.value for k in MemoryKind)
_ENTRY_TYPE_VALUES = tuple(e.value for e in EntryType)


def _parse_entry_type(value: str) -> EntryType:
    """Return the entry type named by ``value``.

    Args:
        value: Entry type name.

    Returns:
        The matching ``EntryType``.

    Raises:
        ValidationError: When ``value`` is not an entry type. Guides have no
            entry type, so ``guide`` is rejected too.
    """
    try:
        return EntryType(value)
    except ValueError:
        raise ValidationError(
            f"type must be one of {', '.join(_ENTRY_TYPE_VALUES)}; got {value!r} "
            "(guides have no entry type: use kind='guide' or guide_find)"
        ) from None


def resolve_type_tokens(
    *,
    types: Sequence[str] | None = None,
    kind: str | None = None,
    entry_type: str | None = None,
) -> list[str] | None:
    """Normalize type filters to tokens (entry types and/or ``guide``).

    Explicit ``types`` wins. Otherwise ``kind`` / ``entry_type`` map to tokens.
    None means no type restriction.

    Raises:
        ValidationError: When ``kind`` is not ``entry`` or ``guide``, or
            ``entry_type`` is not an entry type.
    """
    if types is not None:
        tokens = [t.strip() for t in types if t and t.strip()]
        return tokens or None
    if kind and kind not in _KIND_VALUES:
        raise ValidationError(f"kind must be 'entry' or 'guide'; got {kind!r}")
    if kind == "guide":
        return ["guide"]
    if entry_type:
        _parse_entry_type(entry_type)
    if kind == "entry":
        return [entry_type] if entry_type else [e.value for e in EntryType]
    if entry_type:
        return [entry_type]
    return None


async def publish_and_commit(
    session: AsyncSession, principal: Principal, memory: Memory, action: Action
) -> Memory:
    """Publish the mutation event, commit under RLS and reload the row.

    Args:
        session: Database session holding the pending mutation.
        principal: Authenticated caller.
        memory: The created or updated memory row.
        action: Event action, ``created`` or ``updated``.

    Returns:
        The refreshed memory row.
    """
    await publish_memory_event(session, memory_event_from_row(action, memory))
    await commit_with_rls(session, user_id=principal.user_id, is_admin=principal.is_admin)
    await session.refresh(memory)
    return memory


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
    group: str | None = None,
    visibility: Visibility | None = None,
) -> Memory:
    """Create an entry in a space the caller can write to.

    Without a space, writes to the caller's personal space (or the named or
    default shared group).

    Returns:
        The new memory.

    Raises:
        ServiceError: When the space is not writable, the entry is a
            near-duplicate (unless ``force``), or the input is invalid.
    """
    title = check_title(title)
    body = check_body(body)
    tags = check_tags(tags)
    path_hint = check_optional_text(path_hint, "path_hint", limits.PATH_HINT_MAX)
    repo_hint = check_optional_text(repo_hint, "repo_hint", limits.REPO_HINT_MAX)
    space = await resolve_write_space(
        session, principal, space_id, group=group, visibility=visibility
    )
    et = _parse_entry_type(entry_type)
    expiry = resolve_expiry(expires_at=expires_at, ttl_days=ttl_days, permanent=permanent)
    if not force:
        # Serialise check-then-insert for entries in this space (released at
        # commit or rollback). ``force=True`` skips the check and the lock.
        await advisory_xact_lock(session, dedup_lock_key(space.id, MemoryKind.entry))
        dup = await find_near_duplicate(
            session, space_id=space.id, title=title, body=body, kind=MemoryKind.entry
        )
        if dup:
            raise NearDuplicateError(
                f"Near-duplicate exists (id={dup.id}, title={dup.title!r}). "
                "Update it with revise(id=...) instead; pass force=true only if "
                "this is a different memory.",
                duplicate_id=str(dup.id),
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
        # Parked: provenance is stored empty until a writer is designed (SEC-011).
        provenance={},
        created_by_user_id=principal.user_id,
    )
    session.add(memory)
    await session.flush()
    await session.refresh(memory)
    return await publish_and_commit(session, principal, memory, "created")


async def open_memory(
    session: AsyncSession,
    principal: Principal,
    *,
    memory_id: uuid.UUID | None = None,
    title: str | None = None,
    space_id: uuid.UUID | None = None,
    kind: str | None = None,
    recall: bool = False,
) -> Memory | None:
    """Load one memory the caller can see.

    A title lookup prefers live memories. Past-due memories are used only
    when no live memory has the title, so a title that was remembered again
    after it expired opens the live row.

    Args:
        session: Database session.
        principal: Authenticated caller.
        memory_id: Memory id (preferred).
        title: Exact title within accessible spaces.
        space_id: Optional space for title lookup.
        kind: ``entry`` or ``guide``; narrows a title lookup. Not used with
            ``memory_id``.
        recall: Record this read as a recall (usage + sliding TTL).

    Returns:
        Memory, or None when missing or not visible.

    Raises:
        ValidationError: When ``kind`` is not a memory kind, or when the
            title matches more than one memory.
    """
    wanted_kind: MemoryKind | None = None
    if kind:
        try:
            wanted_kind = MemoryKind(kind)
        except ValueError:
            raise ValidationError("kind must be 'entry' or 'guide'") from None
    memory: Memory | None = None
    if memory_id:
        result = await session.execute(select(Memory).where(Memory.id == memory_id))
        memory = result.scalar_one_or_none()
        if memory and not await get_readable_space(session, principal, memory.space_id):
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
        if wanted_kind is not None:
            stmt = stmt.where(Memory.kind == wanted_kind)
        stmt = stmt.order_by(Memory.updated_at.desc(), Memory.id)
        rows = list((await session.execute(stmt)).scalars())
        now = datetime.now(UTC)
        live = [m for m in rows if m.expires_at is None or m.expires_at > now]
        # Past-due rows are hidden from every other recall path. They count
        # here only when no live row has this title.
        candidates = live or rows
        if len(candidates) > 1:
            choices = ", ".join(
                f"{m.id} ({m.kind.value}, space {m.space_id})" for m in candidates[:8]
            )
            more = "" if len(candidates) <= 8 else f" (+{len(candidates) - 8} more)"
            raise ValidationError(
                f"Title {title!r} matches {len(candidates)} memories; "
                f"pass id, space_id or kind. Matches: {choices}{more}"
            )
        memory = candidates[0] if candidates else None
    if memory and recall:
        await touch_memories(session, principal, [memory.id])
        await session.refresh(memory)
    return memory


async def require_memory_manage(
    session: AsyncSession, principal: Principal, memory: Memory
) -> None:
    """Raise unless the principal may change, forget or relocate ``memory``.

    Allowed when the caller is an admin, the memory creator, or a manager of
    the memory's group (``can_manage_group``). ``revise``, ``forget`` and
    ``relocate`` all call this. ``guide_write`` only upserts guides that the
    caller created.

    Args:
        session: Database session.
        principal: Authenticated caller.
        memory: Already-loaded memory the caller can at least open.

    Raises:
        ForbiddenError: When the caller may not manage the memory.
    """
    if principal.is_admin:
        return
    if (
        principal.user_id
        and memory.created_by_user_id
        and memory.created_by_user_id == principal.user_id
    ):
        return
    space = await get_readable_space(session, principal, memory.space_id)
    if space is not None and can_manage_group(space, principal):
        return
    raise ForbiddenError("Not allowed to manage this memory")


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
    ttl_days: int | None = None,
    permanent: bool = False,
) -> Memory:
    """Patch an entry or guide.

    Allowed for its creator, the group creator, or an administrator.

    Returns:
        The updated memory.

    Raises:
        NotFoundError: When the memory is not readable.
        ForbiddenError: When the caller may not change it.
        ValidationError: On invalid input.
    """
    if title is not None:
        title = check_title(title)
    if body is not None:
        body = check_body(body)
    if tags is not None:
        tags = check_tags(tags)
    path_hint = check_optional_text(path_hint, "path_hint", limits.PATH_HINT_MAX)
    repo_hint = check_optional_text(repo_hint, "repo_hint", limits.REPO_HINT_MAX)
    check_lifetime_args(
        expires_at=expires_at, ttl_days=ttl_days, permanent=permanent or clear_expiry
    )
    memory = await open_memory(session, principal, memory_id=memory_id)
    if not memory:
        raise NotFoundError("Memory not found")
    # Same rule as forget/relocate: creator, group manager, or admin.
    await require_memory_manage(session, principal, memory)
    new_type: EntryType | None = None
    if entry_type is not None:
        # Validate before any field is changed, so a refused call edits nothing.
        if memory.kind == MemoryKind.guide:
            raise ValidationError("Guides have no entry type; omit type when revising a guide")
        new_type = _parse_entry_type(entry_type)
    text_changed = False
    if title is not None:
        memory.title = title.strip()
        text_changed = True
    if body is not None:
        memory.body = body
        text_changed = True
    if new_type is not None:
        memory.entry_type = new_type
    if tags is not None:
        memory.tags = tags
    if clear_expiry or permanent or ttl_days == 0:
        memory.expires_at = None
    elif expires_at is not None:
        memory.expires_at = expires_at
    elif ttl_days is not None:
        memory.expires_at = datetime.now(UTC) + timedelta(days=ttl_days)
    if path_hint is not None:
        memory.path_hint = path_hint or None
    if repo_hint is not None:
        memory.repo_hint = repo_hint or None
    if text_changed:
        memory.embedding = await get_embedder().aembed_memory_text(memory.title, memory.body)
    memory.updated_at = datetime.now(UTC)
    memory.updated_by_user_id = principal.user_id
    return await publish_and_commit(session, principal, memory, "updated")


async def forget(session: AsyncSession, principal: Principal, memory_id: uuid.UUID) -> None:
    """Delete an entry or guide.

    Allowed for its creator, the group creator, or an administrator.

    Raises:
        NotFoundError: When the memory is not readable.
        ForbiddenError: When the caller may not delete it.
    """
    memory = await open_memory(session, principal, memory_id=memory_id)
    if not memory:
        raise NotFoundError("Memory not found")
    await require_memory_manage(session, principal, memory)
    event = memory_event_from_row("deleted", memory)
    await session.delete(memory)
    await publish_memory_event(session, event)
    await commit_with_rls(session, user_id=principal.user_id, is_admin=principal.is_admin)


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
    tags: list[str] | None = None,
) -> list[Memory]:
    """List memories in readable spaces, newest first.

    Returns:
        Digests for the requested page.

    Raises:
        ValidationError: On invalid filters.
    """
    offset = check_offset(offset)
    if tags is not None:
        tags = check_tags(tags)
    space_ids = await accessible_space_ids(session, principal, space_id, space_scope=space_scope)
    if not space_ids:
        return []
    limit = get_settings().resolve_browse_limit(limit)
    stmt = active_select(space_ids, include_expired)
    tokens = resolve_type_tokens(types=types, kind=kind, entry_type=entry_type)
    clause = memory_type_clause(tokens)
    if clause is not None:
        stmt = stmt.where(clause)
    if tags:
        stmt = stmt.where(Memory.tags.contains(tags))
    stmt = stmt.order_by(Memory.updated_at.desc(), Memory.id).limit(limit).offset(offset)
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
    tags: list[str] | None = None,
    offset: int = 0,
) -> list[Digest]:
    """List memories in readable spaces updated in a time window.

    Returns:
        Digests ordered by ``updated_at``.
    """
    if tags is not None:
        tags = check_tags(tags)
    offset = check_offset(offset)
    space_ids = await accessible_space_ids(session, principal, space_id)
    if not space_ids:
        return []
    limit = get_settings().resolve_browse_limit(limit)
    stmt = active_select(space_ids, include_expired)
    if since_dt:
        stmt = stmt.where(Memory.updated_at >= since_dt)
    if until_dt:
        stmt = stmt.where(Memory.updated_at <= until_dt)
    if tags:
        stmt = stmt.where(Memory.tags.contains(tags))
    stmt = stmt.order_by(Memory.updated_at.desc()).limit(limit).offset(max(0, offset))
    rows = list((await session.execute(stmt)).scalars())
    # A listing is not a recall: it does not renew TTL or count as usage.
    return [digest_from_memory(m) for m in rows]


async def by_tag(
    session: AsyncSession,
    principal: Principal,
    *,
    tags: list[str],
    mode: str = "and",
    space_id: uuid.UUID | None = None,
    limit: int | None = None,
    include_expired: bool = False,
    offset: int = 0,
) -> list[Digest]:
    """List memories in readable spaces by tag.

    ``mode`` is ``and`` (every tag must match) or ``or`` (any tag matches).

    Returns:
        Digests, newest first.

    Raises:
        ValidationError: On invalid tags or mode.
    """
    tags = check_tags(tags)
    offset = check_offset(offset)
    if not tags:
        raise ValidationError("tags must contain at least one non-empty tag")
    space_ids = await accessible_space_ids(session, principal, space_id)
    if not space_ids:
        return []
    limit = get_settings().resolve_browse_limit(limit)
    stmt = active_select(space_ids, include_expired)
    normalized = (mode or "and").strip().lower()
    if normalized not in {"and", "or"}:
        raise ValidationError("by_tag mode must be 'and' or 'or'")
    if normalized == "or":
        stmt = stmt.where(Memory.tags.overlap(tags))
    else:
        stmt = stmt.where(Memory.tags.contains(tags))
    stmt = stmt.order_by(Memory.updated_at.desc()).limit(limit).offset(max(0, offset))
    rows = list((await session.execute(stmt)).scalars())
    # A listing is not a recall: it does not renew TTL or count as usage.
    return [digest_from_memory(m) for m in rows]


async def relocate(
    session: AsyncSession,
    principal: Principal,
    memory_id: uuid.UUID,
    target_space_id: uuid.UUID,
) -> Memory:
    """Move an entry or guide to another space.

    Allowed for its creator, the group creator, or an administrator; the
    destination must be writable.

    Returns:
        The moved memory.

    Raises:
        NotFoundError: When the memory is not readable.
        ForbiddenError: When the move is not allowed.
    """
    memory = await open_memory(session, principal, memory_id=memory_id)
    if not memory:
        raise NotFoundError("Memory not found")
    await require_memory_manage(session, principal, memory)
    target = await resolve_write_space(session, principal, target_space_id)
    left = memory_event_from_row("deleted", memory)
    memory.space_id = target.id
    memory.updated_at = datetime.now(UTC)
    memory.updated_by_user_id = principal.user_id
    await publish_memory_event(session, left)
    await publish_memory_event(session, memory_event_from_row("created", memory))
    await commit_with_rls(session, user_id=principal.user_id, is_admin=principal.is_admin)
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
    """Hybrid search over readable spaces (entries by default).

    Returns:
        Digests, best first.

    Raises:
        ValidationError: On invalid filters.
    """
    query = check_query(query)
    offset = check_offset(offset)
    if tags is not None:
        tags = check_tags(tags)
    path_hint = check_optional_text(path_hint, "path_hint", limits.PATH_HINT_MAX)
    repo_hint = check_optional_text(repo_hint, "repo_hint", limits.REPO_HINT_MAX)
    space_ids = await accessible_space_ids(session, principal, space_id, space_scope=space_scope)
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
    return await touch_digests(session, principal, digests)


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
    touch: bool = True,
    tags: list[str] | None = None,
    path_hint: str | None = None,
    repo_hint: str | None = None,
) -> list[Digest]:
    """Hybrid search across entries and/or guides for the GUI/REST surface.

    Hits are renewed (sliding TTL) unless ``touch`` is false.
    """
    query = check_query(query)
    offset = check_offset(offset)
    if tags is not None:
        tags = check_tags(tags)
    path_hint = check_optional_text(path_hint, "path_hint", limits.PATH_HINT_MAX)
    repo_hint = check_optional_text(repo_hint, "repo_hint", limits.REPO_HINT_MAX)
    space_ids = await accessible_space_ids(session, principal, space_id, space_scope=space_scope)
    tokens = resolve_type_tokens(types=types, kind=kind, entry_type=entry_type)
    if tokens is None and kind is None and entry_type is None:
        # No type filter given: REST search covers entries only; guides need an explicit type.
        tokens = [e.value for e in EntryType]
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
    if touch:
        # A search the user asked for is a recall. Callers that re-run a
        # search in the background pass ``touch=False``.
        return await touch_digests(session, principal, digests)
    return digests


async def about_path(
    session: AsyncSession,
    principal: Principal,
    *,
    path: str,
    repo_hint: str | None = None,
    limit: int | None = None,
    include_expired: bool = False,
    space_id: uuid.UUID | None = None,
    offset: int = 0,
) -> list[Digest]:
    """List memories whose ``path_hint`` or body mention ``path``.

    A listing, not a recall: it does not renew TTL or count as usage.

    Args:
        session: Database session.
        principal: Caller.
        path: Substring to look for. Blank means "no path filter".
        repo_hint: Optional substring of ``repo_hint`` to narrow by.
        limit: Maximum rows (capped by settings).
        include_expired: Include past-due rows.
        space_id: Limit the listing to one readable space.
        offset: Rows to skip, for paging.

    Returns:
        Newest-first digests. Empty when ``path`` and ``repo_hint`` are both
        blank: a blank pattern would otherwise match every memory.
    """
    path = check_text(path, "path", limits.PATH_HINT_MAX)
    repo_hint = check_optional_text(repo_hint, "repo_hint", limits.REPO_HINT_MAX)
    offset = check_offset(offset)
    needle = (path or "").strip()
    repo = (repo_hint or "").strip()
    if not needle and not repo:
        return []
    space_ids = await accessible_space_ids(session, principal, space_id)
    if not space_ids:
        return []
    limit = get_settings().resolve_search_limit(limit)
    stmt = active_select(space_ids, include_expired)
    if needle:
        path_pat = f"%{escape_ilike(needle)}%"
        stmt = stmt.where(
            or_(
                Memory.path_hint.ilike(path_pat, escape="\\"),
                Memory.body.ilike(path_pat, escape="\\"),
            ),
        )
    if repo:
        stmt = stmt.where(Memory.repo_hint.ilike(f"%{escape_ilike(repo)}%", escape="\\"))
    stmt = stmt.order_by(Memory.updated_at.desc()).limit(limit).offset(max(0, offset))
    rows = list((await session.execute(stmt)).scalars())
    return [digest_from_memory(m) for m in rows]


async def trace(
    session: AsyncSession, principal: Principal, memory_id: uuid.UUID
) -> dict[str, Any]:
    """Return who created and last changed a memory, and when.

    Raises:
        NotFoundError: When the memory is not readable.
    """
    memory = await open_memory(session, principal, memory_id=memory_id)
    if not memory:
        raise NotFoundError("Memory not found")
    data = {
        "id": str(memory.id),
        "title": memory.title,
        "kind": memory.kind.value,
        "created_by": str(memory.created_by_user_id) if memory.created_by_user_id else None,
        "created_at": memory.created_at.isoformat() if memory.created_at else None,
        "updated_by": str(memory.updated_by_user_id) if memory.updated_by_user_id else None,
        "updated_at": memory.updated_at.isoformat() if memory.updated_at else None,
        "provenance": memory.provenance or {},
        "space_id": str(memory.space_id),
    }
    return (await annotate_rows(session, [data]))[0]


async def backfill_orphan_creators(session: AsyncSession) -> int:
    """Attribute memories that have no creator to the creator of their space.

    A memory loses its creator when that user is deleted. The space creator is
    used, never the env admin, so deleted-owner history is not hidden.

    Binds its own admin RLS context. Callers at startup have usually committed
    already, and every commit clears the context; without a context the UPDATE
    matches no rows and raises no error.

    Args:
        session: Database session. Any open transaction is committed.

    Returns:
        Number of memories updated.
    """
    await set_rls_context(session, user_id=None, is_admin=True)
    result = await session.execute(
        text(
            """
            UPDATE memories AS m
            SET created_by_user_id = s.created_by_user_id
            FROM spaces AS s
            WHERE m.space_id = s.id
              AND m.created_by_user_id IS NULL
              AND s.created_by_user_id IS NOT NULL
            """
        )
    )
    # rowcount is typed on CursorResult only; read it before the commit.
    updated = result.rowcount if isinstance(result, CursorResult) else 0
    await commit_with_rls(session, user_id=None, is_admin=True)
    return updated
