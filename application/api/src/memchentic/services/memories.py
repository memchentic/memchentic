from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import numpy as np
from sqlalchemy import (
    CursorResult,
    DateTime,
    Select,
    and_,
    case,
    func,
    literal,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import Principal
from memchentic.db.session import advisory_xact_lock, commit_with_rls, set_rls_context
from memchentic.embeddings import get_embedder
from memchentic.events import memory_event_from_row, publish_memory_event
from memchentic.models.entities import EntryType, Memory, MemoryKind, Space, User
from memchentic.search.hybrid import (
    GUIDE_CONFIDENCE_MAX,
    Digest,
    active_memory_clause,
    escape_ilike,
    hybrid_search,
    make_snippet,
    memory_type_clause,
    prepare_filtered_vector_scan,
)
from memchentic.services import limits
from memchentic.services.errors import (
    ForbiddenError,
    NearDuplicateError,
    NotFoundError,
    ValidationError,
)
from memchentic.services.spaces import (
    Visibility,
    can_manage_group,
    get_readable_space,
    list_spaces,
    resolve_write_space,
)
from memchentic.services.validation import (
    check_body,
    check_offset,
    check_optional_text,
    check_query,
    check_tags,
    check_text,
    check_title,
    check_ttl_days,
    parse_space_scope,
)
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
        "updated_by": str(m.updated_by_user_id) if m.updated_by_user_id else None,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
        "provenance": m.provenance or {},
    }
    if full:
        data["body"] = m.body
    else:
        data["snippet"] = make_snippet(m.body)
    return data


def check_lifetime_args(
    *, expires_at: datetime | None, ttl_days: int | None, permanent: bool
) -> None:
    """Reject contradictory lifetime arguments.

    Args:
        expires_at: Absolute expiry supplied by the caller.
        ttl_days: Relative lifetime in days; ``0`` means permanent.
        permanent: Caller wants no expiry.

    Raises:
        ValidationError: When ``ttl_days`` is outside
            ``0..limits.TTL_DAYS_MAX`` or the arguments contradict each other.
    """
    check_ttl_days(ttl_days)
    if expires_at is not None and (permanent or ttl_days is not None):
        raise ValidationError("Pass only one of expires_at, ttl_days or permanent")
    if permanent and ttl_days:
        raise ValidationError("permanent=true contradicts ttl_days > 0; pass only one")


def resolve_expiry(
    *,
    expires_at: datetime | None,
    ttl_days: int | None,
    permanent: bool,
    now: datetime | None = None,
) -> datetime | None:
    """Pick the create-time expiry for a new memory.

    At most one of ``expires_at``, ``ttl_days`` and ``permanent`` may be set
    (``permanent`` with ``ttl_days=0`` is allowed: both mean no expiry). With
    none set the lifetime is ``MEMORY_DEFAULT_TTL_DAYS``.

    Args:
        expires_at: Absolute expiry supplied by the caller.
        ttl_days: Relative lifetime in days; ``0`` means permanent.
        permanent: Caller wants no expiry.
        now: Reference time; defaults to the current UTC time.

    Returns:
        Expiry timestamp, or None for a permanent memory.

    Raises:
        ValidationError: When ``ttl_days`` is outside
            ``0..limits.TTL_DAYS_MAX`` or the arguments contradict each other.
    """
    check_lifetime_args(expires_at=expires_at, ttl_days=ttl_days, permanent=permanent)
    if expires_at is not None:
        return expires_at
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
        "updated_by": d.updated_by,
        "updated_at": d.updated_at,
        "expires_at": d.expires_at,
        "confidence": d.confidence,
        "created_at": d.created_at,
        "path_hint": d.path_hint,
        "repo_hint": d.repo_hint,
    }


async def enrich_creator_fields(
    session: AsyncSession, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Attach creator and last-editor labels from users.

    Adds ``created_by_email`` / ``created_by_display_name`` and
    ``updated_by_email`` / ``updated_by_display_name``.

    Args:
        session: DB session.
        rows: Memory or digest dicts that may include ``created_by`` and
            ``updated_by``.

    Returns:
        The same list, mutated in place with the label fields.
    """
    raw_ids: set[uuid.UUID] = set()
    for row in rows:
        for key in ("created_by", "updated_by"):
            value = row.get(key)
            if not value:
                continue
            try:
                raw_ids.add(uuid.UUID(str(value)))
            except ValueError:
                continue
    by_id: dict[str, User] = {}
    if raw_ids:
        result = await session.execute(select(User).where(User.id.in_(raw_ids)))
        by_id = {str(u.id): u for u in result.scalars().all()}
    for row in rows:
        creator = by_id.get(str(row.get("created_by") or ""))
        row["created_by_email"] = creator.email if creator else None
        row["created_by_display_name"] = creator.display_name if creator else None
        editor = by_id.get(str(row.get("updated_by") or ""))
        row["updated_by_email"] = editor.email if editor else None
        row["updated_by_display_name"] = editor.display_name if editor else None
    return rows


CONTENT_TRUST = "untrusted"


async def annotate_rows(session: AsyncSession, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach author, space and trust labels to serialized memory rows.

    Adds the creator and editor labels of ``enrich_creator_fields``,
    ``space_name``, ``space_kind`` and the constant ``content_trust`` marker.
    Existing keys are never changed.

    Args:
        session: DB session with the caller's RLS context.
        rows: Memory or digest dicts that carry ``created_by`` and ``space_id``.

    Returns:
        The same list, mutated in place.
    """
    await enrich_creator_fields(session, rows)
    space_ids: set[uuid.UUID] = set()
    for row in rows:
        sid = row.get("space_id")
        if not sid:
            continue
        try:
            space_ids.add(uuid.UUID(str(sid)))
        except ValueError:
            continue
    spaces: dict[str, tuple[str | None, str | None]] = {}
    if space_ids:
        result = await session.execute(
            select(Space.id, Space.name, Space.kind).where(Space.id.in_(space_ids))
        )
        spaces = {str(sid): (name, kind.value) for sid, name, kind in result.all()}
    for row in rows:
        name, kind = spaces.get(str(row.get("space_id") or ""), (None, None))
        row["space_name"] = name
        row["space_kind"] = kind
        row["content_trust"] = CONTENT_TRUST
    return rows


async def memory_out(
    session: AsyncSession, memory: Memory, *, full: bool = False
) -> dict[str, Any]:
    """Serialize one memory for REST or MCP, with author, space and trust labels.

    Args:
        session: DB session with the caller's RLS context.
        memory: Loaded memory row.
        full: Include ``body`` instead of ``snippet``.

    Returns:
        The labelled memory dict.
    """
    rows = await annotate_rows(session, [memory_to_dict(memory, full=full)])
    return rows[0]


async def memories_out(session: AsyncSession, memories: Sequence[Memory]) -> list[dict[str, Any]]:
    """Serialize memories as list rows, with author, space and trust labels.

    Args:
        session: DB session with the caller's RLS context.
        memories: Loaded memory rows.

    Returns:
        Labelled memory dicts in the same order.
    """
    return await annotate_rows(session, [memory_to_dict(m) for m in memories])


async def digests_out(session: AsyncSession, digests: Sequence[Digest]) -> list[dict[str, Any]]:
    """Serialize digests, with author, space and trust labels.

    Args:
        session: DB session with the caller's RLS context.
        digests: Search or listing digests.

    Returns:
        Labelled digest dicts in the same order.
    """
    return await annotate_rows(session, [digest_to_dict(d) for d in digests])


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

    Raises:
        NotFoundError: When ``space_id`` is set and the caller cannot read
            that space.
    """
    if space_id:
        space = await get_readable_space(session, principal, space_id)
        if not space:
            raise NotFoundError("Space not found or not accessible")
        return [space.id]
    spaces = await list_spaces(session, principal)
    if space_scope:
        kind = parse_space_scope(space_scope)
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


def _active_select(space_ids: list[uuid.UUID], include_expired: bool) -> Select[Memory]:
    """Memories in ``space_ids``, hiding past-due rows unless ``include_expired``."""
    stmt = select(Memory).where(Memory.space_id.in_(space_ids))
    active = active_memory_clause(include_expired=include_expired)
    if active is not None:
        stmt = stmt.where(active)
    return stmt


def _dedup_lock_key(space_id: uuid.UUID, kind: MemoryKind) -> str:
    """Advisory-lock name that serialises near-duplicate check + write per space and kind."""
    return f"memchentic:dedup:{space_id}:{kind.value}"


async def find_near_duplicate(
    session: AsyncSession,
    *,
    space_id: uuid.UUID,
    title: str,
    body: str,
    kind: MemoryKind,
    created_by: uuid.UUID | None = None,
) -> Memory | None:
    """Find an active near-duplicate by embedding similarity or exact title.

    Past-due rows are ignored so expired memories do not block ``remember`` or
    become silent ``guide_write`` upsert targets. Exact title is checked across
    the whole space (not only the nearest-vector window).

    Args:
        session: Database session.
        space_id: Space to search.
        title: Candidate title.
        body: Candidate body.
        kind: Entry or guide.
        created_by: When set, only memories created by this user match.
            ``guide_write`` passes the caller so an upsert never replaces
            another member's guide.

    Returns:
        The matching memory, or None.
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
    if created_by is not None:
        title_stmt = title_stmt.where(Memory.created_by_user_id == created_by)
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
    if created_by is not None:
        stmt = stmt.where(Memory.created_by_user_id == created_by)
    if active is not None:
        stmt = stmt.where(active)
    await prepare_filtered_vector_scan(session)
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
        await advisory_xact_lock(session, _dedup_lock_key(space.id, MemoryKind.entry))
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
        provenance=provenance or {},
        created_by_user_id=principal.user_id,
    )
    session.add(memory)
    await session.flush()
    await session.refresh(memory)
    await publish_memory_event(session, memory_event_from_row("created", memory))
    await commit_with_rls(session, user_id=principal.user_id, is_admin=principal.is_admin)
    await session.refresh(memory)
    return memory


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
    await publish_memory_event(session, memory_event_from_row("updated", memory))
    await commit_with_rls(session, user_id=principal.user_id, is_admin=principal.is_admin)
    await session.refresh(memory)
    return memory


async def forget(session: AsyncSession, principal: Principal, memory_id: uuid.UUID) -> None:
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
    offset = check_offset(offset)
    if tags is not None:
        tags = check_tags(tags)
    space_ids = await accessible_space_ids(session, principal, space_id, space_scope=space_scope)
    if not space_ids:
        return []
    limit = get_settings().resolve_browse_limit(limit)
    stmt = _active_select(space_ids, include_expired)
    tokens = resolve_type_tokens(types=types, kind=kind, entry_type=entry_type)
    clause = memory_type_clause(tokens)
    if clause is not None:
        stmt = stmt.where(clause)
    if tags:
        stmt = stmt.where(Memory.tags.contains(tags))
    stmt = stmt.order_by(Memory.updated_at.desc(), Memory.id).limit(limit).offset(offset)
    return list((await session.execute(stmt)).scalars())


def _digest_from_memory(m: Memory, *, score: float = 1.0) -> Digest:
    return Digest(
        id=str(m.id),
        title=m.title,
        score=score,
        snippet=make_snippet(m.body),
        type=m.entry_type.value if m.entry_type else None,
        tags=list(m.tags or []),
        space_id=str(m.space_id),
        kind=m.kind.value,
        created_by=_creator_id(m),
        updated_by=str(m.updated_by_user_id) if m.updated_by_user_id else None,
        updated_at=m.updated_at.isoformat() if m.updated_at else None,
        expires_at=m.expires_at.isoformat() if m.expires_at else None,
        confidence=m.confidence,
        created_at=m.created_at.isoformat() if m.created_at else None,
        path_hint=m.path_hint,
        repo_hint=m.repo_hint,
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
    tags: list[str] | None = None,
    offset: int = 0,
) -> list[Digest]:
    if tags is not None:
        tags = check_tags(tags)
    offset = check_offset(offset)
    space_ids = await accessible_space_ids(session, principal, space_id)
    if not space_ids:
        return []
    limit = get_settings().resolve_browse_limit(limit)
    stmt = _active_select(space_ids, include_expired)
    if since_dt:
        stmt = stmt.where(Memory.updated_at >= since_dt)
    if until_dt:
        stmt = stmt.where(Memory.updated_at <= until_dt)
    if tags:
        stmt = stmt.where(Memory.tags.contains(tags))
    stmt = stmt.order_by(Memory.updated_at.desc()).limit(limit).offset(max(0, offset))
    rows = list((await session.execute(stmt)).scalars())
    # A listing is not a recall: it does not renew TTL or count as usage.
    return [_digest_from_memory(m) for m in rows]


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
    tags = check_tags(tags)
    offset = check_offset(offset)
    if not tags:
        raise ValidationError("tags must contain at least one non-empty tag")
    space_ids = await accessible_space_ids(session, principal, space_id)
    if not space_ids:
        return []
    limit = get_settings().resolve_browse_limit(limit)
    stmt = _active_select(space_ids, include_expired)
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
    return [_digest_from_memory(m) for m in rows]


async def relocate(
    session: AsyncSession,
    principal: Principal,
    memory_id: uuid.UUID,
    target_space_id: uuid.UUID,
) -> Memory:
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
        # Default REST search historically searched entries only.
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
        return await _touch_digests(session, principal, digests)
    return digests


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
    """Create a guide, or reinforce a near-duplicate guide of the caller.

    Reinforcement raises confidence, counts as a recall, and slides the expiry
    (an explicit ``expires_at`` / ``ttl_days`` / ``permanent`` replaces it).
    Only guides created by the caller are upsert targets. A title or text
    match on another member's guide creates a separate guide; use ``revise``
    by id (creator, group manager, or admin) to change someone else's guide.
    """
    title = check_title(title)
    body = check_body(body)
    if tags is not None:
        tags = check_tags(tags)
    check_lifetime_args(expires_at=expires_at, ttl_days=ttl_days, permanent=permanent)
    space = await resolve_write_space(
        session, principal, space_id, group=group, visibility=visibility
    )
    explicit_ttl = expires_at is not None or ttl_days is not None or permanent
    # Serialise check-then-write for guides in this space. Postgres releases
    # the lock at the commit below (or at rollback), so overlapping identical
    # writes run one after another and each later one sees the earlier row.
    await advisory_xact_lock(session, _dedup_lock_key(space.id, MemoryKind.guide))
    dup = await find_near_duplicate(
        session,
        space_id=space.id,
        title=title,
        body=body,
        kind=MemoryKind.guide,
        created_by=principal.user_id,
    )
    if dup:
        now = datetime.now(UTC)
        dup.confidence = min(GUIDE_CONFIDENCE_MAX, float(dup.confidence or 1.0) + 1.0)
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
            dup.expires_at = slide_expiry(dup.expires_at, created_at=dup.created_at, now=now)
        dup.recall_count = (dup.recall_count or 0) + 1
        dup.last_recalled_at = now
        dup.embedding = await get_embedder().aembed_memory_text(dup.title, dup.body)
        dup.updated_at = now
        dup.updated_by_user_id = principal.user_id
        await publish_memory_event(session, memory_event_from_row("updated", dup))
        await commit_with_rls(session, user_id=principal.user_id, is_admin=principal.is_admin)
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
    await commit_with_rls(session, user_id=principal.user_id, is_admin=principal.is_admin)
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
    tags: list[str] | None = None,
    offset: int = 0,
) -> list[Digest]:
    query = check_query(query)
    if tags is not None:
        tags = check_tags(tags)
    offset = check_offset(offset)
    digests = await hybrid_search(
        session,
        query=query,
        space_ids=await accessible_space_ids(session, principal, space_id, space_scope=space_scope),
        type_tokens=["guide"],
        tags=tags,
        limit=limit,
        offset=offset,
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
    stmt = _active_select(space_ids, include_expired)
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
    return [_digest_from_memory(m) for m in rows]


async def trace(
    session: AsyncSession, principal: Principal, memory_id: uuid.UUID
) -> dict[str, Any]:
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


async def overlap_report(
    session: AsyncSession,
    principal: Principal,
    *,
    space_id: uuid.UUID | None = None,
    limit: int | None = None,
    include_expired: bool = False,
    expiring_within_days: int | None = None,
) -> dict[str, Any]:
    """Report duplicate candidates and soon-to-expire memories (read-only).

    Args:
        session: Database session.
        principal: Authenticated caller.
        space_id: Limit the report to one space.
        limit: Most pairs and most expiring memories to return.
        include_expired: Also compare past-due memories.
        expiring_within_days: List only memories that expire within this many
            days; ``None`` lists the next ``limit`` expiries.

    Returns:
        ``near_duplicates`` (pairs), ``expiring``, ``conflicts`` (always
        empty; conflict detection is not implemented), ``scanned`` (number
        of memories compared) and ``truncated`` (True when more memories
        exist than ``OVERLAP_SCAN_LIMIT``; the oldest were not compared).
    """
    settings = get_settings()
    if expiring_within_days is not None and expiring_within_days < 0:
        raise ValidationError("expiring_within_days must be >= 0")
    limit = settings.resolve_search_limit(limit)
    note = (
        "Read-only hygiene report; merge via revise/forget. "
        "Conflict detection is not implemented; conflicts is always empty."
    )
    space_ids = await accessible_space_ids(session, principal, space_id)
    if not space_ids:
        return {
            "near_duplicates": [],
            "expiring": [],
            "conflicts": [],
            "scanned": 0,
            "truncated": False,
            "note": note,
            "near_dup_threshold": settings.near_dup_threshold,
            "content_trust": CONTENT_TRUST,
        }

    scan_limit = settings.overlap_scan_limit
    # One row more than the limit shows whether the scan is complete.
    rows = list(
        (
            await session.execute(
                _active_select(space_ids, include_expired)
                .where(Memory.embedding.is_not(None))
                .order_by(Memory.updated_at.desc(), Memory.id)
                .limit(scan_limit + 1)
            )
        ).scalars()
    )
    truncated = len(rows) > scan_limit
    rows = rows[:scan_limit]

    near_duplicates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def _add(a: Memory, b: Memory, reason: str, similarity: float | None = None) -> None:
        first, second = sorted((str(a.id), str(b.id)))
        key = (first, second)
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
    if expiring_within_days is not None:
        expiring_stmt = expiring_stmt.where(
            Memory.expires_at <= now + timedelta(days=expiring_within_days)
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
        "scanned": len(rows),
        "truncated": truncated,
        "note": note,
        "near_dup_threshold": settings.near_dup_threshold,
        "content_trust": CONTENT_TRUST,
    }


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
