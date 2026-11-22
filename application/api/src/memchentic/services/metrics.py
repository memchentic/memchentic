"""ACL-scoped memory corpus and retrieval aggregates for the Metrics GUI."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from sqlalchemy import ColumnElement, String, and_, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import Principal
from memchentic.models.entities import Memory, MemoryKind, MemoryRecall, Project, Space, User
from memchentic.search.hybrid import escape_ilike, memory_type_clause
from memchentic.services.errors import ValidationError
from memchentic.services.memories import resolve_type_tokens
from memchentic.services.spaces import accessible_space_ids
from memchentic.services.validation import check_tags, parse_datetime

MetricRange = Literal["7d", "30d", "90d", "all", "custom"]

_RANGE_DAYS: dict[str, int | None] = {
    "7d": 7,
    "30d": 30,
    "90d": 90,
    "all": None,
}

_EXPIRING_DAYS = 14
_TOP_SPACES = 10
_TOP_TAGS = 15
_TOP_CREATORS = 10
_TOP_ACCESSED = 15
_TOP_PROJECTS = 10
_MAX_WINDOW_DAYS = 366


def _parse_range(raw: str | None) -> Literal["7d", "30d", "90d", "all"]:
    """Normalize a range query token (presets only)."""
    if raw is None or raw == "":
        return "30d"
    if raw not in _RANGE_DAYS:
        raise ValidationError(f"Invalid range {raw!r}; expected 7d, 30d, 90d, or all")
    return cast(Literal["7d", "30d", "90d", "all"], raw)


def _resolve_window(
    range_token: Literal["7d", "30d", "90d", "all"],
    *,
    from_dt: datetime | None,
    to_dt: datetime | None,
    now: datetime,
) -> tuple[MetricRange, datetime | None, datetime | None]:
    """Resolve the metrics time window.

    When ``from_dt`` or ``to_dt`` is set, they define a custom window (preset
    ignored for bounds). Otherwise a preset maps to UTC midnights.

    Returns:
        ``(range_label, window_start_inclusive, window_end_exclusive)``.
        Both bounds are ``None`` for ``all``.

    Raises:
        ValidationError: When the custom window is inverted or longer than
            ``_MAX_WINDOW_DAYS``.
    """
    if from_dt is not None or to_dt is not None:
        end = to_dt or now
        start = from_dt
        if start is None:
            start = (end - timedelta(days=29)).replace(hour=0, minute=0, second=0, microsecond=0)
        if end <= start:
            raise ValidationError("to must be after from")
        # Exclusive end: if the client sent a day boundary at midnight equal
        # to ``to``, treat it as end-of-that-day by bumping to next midnight
        # when the duration would otherwise be zero-width on a date-only feel.
        span_days = (end - start).total_seconds() / 86400.0
        if span_days > _MAX_WINDOW_DAYS:
            raise ValidationError(f"Time window may be at most {_MAX_WINDOW_DAYS} days")
        return "custom", start, end

    days = _RANGE_DAYS[range_token]
    if days is None:
        return "all", None, None
    start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return range_token, start, now


def _day_key(day_val: Any) -> str | None:
    """Normalize a date_trunc result to a YYYY-MM-DD string."""
    if day_val is None:
        return None
    if hasattr(day_val, "astimezone"):
        return day_val.astimezone(UTC).date().isoformat()
    return str(day_val)[:10]


def _empty_payload(
    range_label: MetricRange,
    *,
    window_start: datetime | None,
    window_end: datetime | None,
    now: datetime,
) -> dict[str, Any]:
    """Return a zeroed metrics payload for an empty accessible corpus."""
    created_per_day: list[dict[str, Any]] = []
    accessed_per_day: list[dict[str, Any]] = []
    if window_start is not None and window_end is not None:
        cursor = window_start.replace(hour=0, minute=0, second=0, microsecond=0)
        end_day = window_end
        while cursor < end_day:
            key = cursor.date().isoformat()
            created_per_day.append({"day": key, "entries": 0, "guides": 0})
            accessed_per_day.append({"day": key, "count": 0})
            cursor += timedelta(days=1)
            # Guard runaway custom windows already capped, but keep bounded.
            if len(created_per_day) > _MAX_WINDOW_DAYS + 1:
                break
    return {
        "range": range_label,
        "window": {
            "from": window_start.isoformat() if window_start else None,
            "to": window_end.isoformat() if window_end else None,
            "range": range_label,
        },
        "generated_at": now.isoformat(),
        "totals": {
            "memories": 0,
            "entries": 0,
            "guides": 0,
            "expiring_soon": 0,
            "spaces": 0,
            "created_in_window": 0,
            "accesses_in_window": 0,
            "unique_memories_accessed": 0,
            "never_accessed": 0,
        },
        "created_per_day": created_per_day,
        "accessed_per_day": accessed_per_day,
        "by_kind": [],
        "by_type": [],
        "by_space": [],
        "by_project": [],
        "top_tags": [],
        "top_creators": [],
        "top_accessed": [],
        "accesses_by_source": [],
    }


def _facet_clauses(
    *,
    space_ids: list[uuid.UUID],
    kind: str | None,
    types: Sequence[str] | None,
    tags: list[str] | None,
    project_id: uuid.UUID | None,
    q: str | None,
) -> list[ColumnElement[bool]]:
    """Build Memory facet predicates (ACL space set already applied)."""
    clauses: list[ColumnElement[bool]] = [Memory.space_id.in_(space_ids)]
    tokens = resolve_type_tokens(types=types, kind=kind, entry_type=None)
    type_clause = memory_type_clause(tokens)
    if type_clause is not None:
        clauses.append(type_clause)
    if tags:
        clauses.append(Memory.tags.contains(tags))
    if project_id is not None:
        clauses.append(Memory.project_id == project_id)
    if q:
        pattern = f"%{escape_ilike(q.strip())}%"
        clauses.append(Memory.title.ilike(pattern, escape="\\"))
    return clauses


def _and(clauses: list[ColumnElement[bool]]) -> ColumnElement[bool]:
    if len(clauses) == 1:
        return clauses[0]
    return and_(*clauses)


def _window_created_clauses(
    base: ColumnElement[bool],
    window_start: datetime | None,
    window_end: datetime | None,
) -> ColumnElement[bool]:
    """Facet base plus created_at inside the window when bounded."""
    if window_start is None or window_end is None:
        return base
    return and_(base, Memory.created_at >= window_start, Memory.created_at < window_end)


async def _totals(
    session: AsyncSession,
    base: ColumnElement[bool],
    windowed: ColumnElement[bool],
    now: datetime,
) -> dict[str, int]:
    """Corpus KPIs (facet-scoped) plus created_in_window."""
    soon = now + timedelta(days=_EXPIRING_DAYS)
    totals_row = (
        await session.execute(
            select(
                func.count().label("total"),
                func.count().filter(Memory.kind == MemoryKind.entry).label("entries"),
                func.count().filter(Memory.kind == MemoryKind.guide).label("guides"),
                func.count()
                .filter(
                    Memory.expires_at.is_not(None),
                    Memory.expires_at >= now,
                    Memory.expires_at <= soon,
                )
                .label("expiring"),
                func.count(func.distinct(Memory.space_id)).label("spaces_used"),
                func.count().filter(Memory.recall_count == 0).label("never_accessed"),
            )
            .select_from(Memory)
            .where(base)
        )
    ).one()
    created_in_window = int(
        (
            await session.execute(select(func.count()).select_from(Memory).where(windowed))
        ).scalar_one()
        or 0
    )
    return {
        "memories": int(totals_row.total or 0),
        "entries": int(totals_row.entries or 0),
        "guides": int(totals_row.guides or 0),
        "expiring_soon": int(totals_row.expiring or 0),
        "spaces": int(totals_row.spaces_used or 0),
        "created_in_window": created_in_window,
        "never_accessed": int(totals_row.never_accessed or 0),
    }


def _by_kind_from_counts(entries: int, guides: int) -> list[dict[str, Any]]:
    kind_counts = [(MemoryKind.entry, entries), (MemoryKind.guide, guides)]
    return [
        {"kind": kind.value, "count": count}
        for kind, count in sorted(kind_counts, key=lambda pair: pair[1], reverse=True)
        if count
    ]


async def _by_kind(session: AsyncSession, windowed: ColumnElement[bool]) -> list[dict[str, Any]]:
    """Count memories per kind inside the (possibly windowed) set."""
    row = (
        await session.execute(
            select(
                func.count().filter(Memory.kind == MemoryKind.entry).label("entries"),
                func.count().filter(Memory.kind == MemoryKind.guide).label("guides"),
            )
            .select_from(Memory)
            .where(windowed)
        )
    ).one()
    return _by_kind_from_counts(int(row.entries or 0), int(row.guides or 0))


async def _by_type(session: AsyncSession, windowed: ColumnElement[bool]) -> list[dict[str, Any]]:
    type_rows = (
        await session.execute(
            select(Memory.entry_type, func.count())
            .where(windowed, Memory.kind == MemoryKind.entry, Memory.entry_type.is_not(None))
            .group_by(Memory.entry_type)
            .order_by(func.count().desc())
        )
    ).all()
    return [{"type": t.value, "count": int(c)} for t, c in type_rows if t is not None]


async def _by_space(session: AsyncSession, windowed: ColumnElement[bool]) -> list[dict[str, Any]]:
    space_rows = (
        await session.execute(
            select(Memory.space_id, Space.name, func.count())
            .join(Space, Space.id == Memory.space_id)
            .where(windowed)
            .group_by(Memory.space_id, Space.name)
            .order_by(func.count().desc())
        )
    ).all()
    by_space: list[dict[str, Any]] = []
    other = 0
    for idx, (sid, name, count) in enumerate(space_rows):
        if idx < _TOP_SPACES:
            by_space.append({"space_id": str(sid), "name": name, "count": int(count)})
        else:
            other += int(count)
    if other:
        by_space.append({"space_id": None, "name": "Other", "count": other})
    return by_space


async def _by_project(session: AsyncSession, windowed: ColumnElement[bool]) -> list[dict[str, Any]]:
    project_rows = (
        await session.execute(
            select(Memory.project_id, Project.name, func.count())
            .outerjoin(Project, Project.id == Memory.project_id)
            .where(windowed, Memory.project_id.is_not(None))
            .group_by(Memory.project_id, Project.name)
            .order_by(func.count().desc())
            .limit(_TOP_PROJECTS)
        )
    ).all()
    return [
        {
            "project_id": str(pid),
            "name": name or "Unknown",
            "count": int(c),
        }
        for pid, name, c in project_rows
        if pid is not None
    ]


async def _top_tags(session: AsyncSession, windowed: ColumnElement[bool]) -> list[dict[str, Any]]:
    tag_sq = (
        select(func.unnest(Memory.tags, type_=String).label("tag"))
        .where(windowed, func.cardinality(Memory.tags) > 0)
        .subquery()
    )
    tag_rows = (
        await session.execute(
            select(tag_sq.c.tag, func.count())
            .where(tag_sq.c.tag != "")
            .group_by(tag_sq.c.tag)
            .order_by(func.count().desc(), tag_sq.c.tag.asc())
            .limit(_TOP_TAGS)
        )
    ).all()
    return [{"tag": tag, "count": int(count)} for tag, count in tag_rows]


async def _top_creators(
    session: AsyncSession, windowed: ColumnElement[bool]
) -> list[dict[str, Any]]:
    creator_rows = (
        await session.execute(
            select(
                Memory.created_by_user_id,
                User.email,
                User.display_name,
                func.count(),
            )
            .outerjoin(User, User.id == Memory.created_by_user_id)
            .where(windowed, Memory.created_by_user_id.is_not(None))
            .group_by(Memory.created_by_user_id, User.email, User.display_name)
            .order_by(func.count().desc())
            .limit(_TOP_CREATORS)
        )
    ).all()
    return [
        {
            "user_id": str(uid),
            "email": email,
            "display_name": display_name,
            "count": int(c),
        }
        for uid, email, display_name, c in creator_rows
    ]


def _zero_fill_days(
    window_start: datetime | None,
    window_end: datetime | None,
    by_day: dict[str, dict[str, int]],
    *,
    sparse_ok: bool,
) -> list[dict[str, Any]]:
    """Build created_per_day rows, zero-filling when the window is bounded."""
    created_per_day: list[dict[str, Any]] = []
    if window_start is not None and window_end is not None and not sparse_ok:
        cursor = window_start.replace(hour=0, minute=0, second=0, microsecond=0)
        while cursor < window_end:
            d = cursor.date().isoformat()
            bucket = by_day.get(d, {"entries": 0, "guides": 0})
            created_per_day.append(
                {"day": d, "entries": bucket["entries"], "guides": bucket["guides"]}
            )
            cursor += timedelta(days=1)
            if len(created_per_day) > _MAX_WINDOW_DAYS + 1:
                break
    else:
        for d in sorted(by_day.keys()):
            bucket = by_day[d]
            created_per_day.append(
                {"day": d, "entries": bucket["entries"], "guides": bucket["guides"]}
            )
    return created_per_day


async def _created_per_day(
    session: AsyncSession,
    base: ColumnElement[bool],
    window_start: datetime | None,
    window_end: datetime | None,
) -> list[dict[str, Any]]:
    day_expr = func.date_trunc("day", Memory.created_at, literal_column("'UTC'"))
    day_filters: list[ColumnElement[bool]] = [base]
    if window_start is not None:
        day_filters.append(Memory.created_at >= window_start)
    if window_end is not None:
        day_filters.append(Memory.created_at < window_end)

    day_rows = (
        await session.execute(
            select(day_expr.label("day"), Memory.kind, func.count())
            .where(*day_filters)
            .group_by(day_expr, Memory.kind)
            .order_by(day_expr)
        )
    ).all()

    by_day: dict[str, dict[str, int]] = {}
    for day_val, kind, count in day_rows:
        key = _day_key(day_val)
        if key is None:
            continue
        bucket = by_day.setdefault(key, {"entries": 0, "guides": 0})
        if kind.value == MemoryKind.guide.value:
            bucket["guides"] += int(count)
        else:
            bucket["entries"] += int(count)

    sparse = window_start is None
    return _zero_fill_days(window_start, window_end, by_day, sparse_ok=sparse)


async def _accessed_per_day(
    session: AsyncSession,
    memory_filter: ColumnElement[bool],
    window_start: datetime | None,
    window_end: datetime | None,
) -> list[dict[str, Any]]:
    day_expr = func.date_trunc("day", MemoryRecall.recalled_at, literal_column("'UTC'"))
    filters: list[ColumnElement[bool]] = [
        MemoryRecall.memory_id == Memory.id,
        memory_filter,
    ]
    if window_start is not None:
        filters.append(MemoryRecall.recalled_at >= window_start)
    if window_end is not None:
        filters.append(MemoryRecall.recalled_at < window_end)

    day_rows = (
        await session.execute(
            select(day_expr.label("day"), func.count())
            .select_from(MemoryRecall)
            .join(Memory, Memory.id == MemoryRecall.memory_id)
            .where(*filters)
            .group_by(day_expr)
            .order_by(day_expr)
        )
    ).all()

    by_day: dict[str, int] = {}
    for day_val, count in day_rows:
        key = _day_key(day_val)
        if key is None:
            continue
        by_day[key] = int(count)

    accessed: list[dict[str, Any]] = []
    if window_start is not None and window_end is not None:
        cursor = window_start.replace(hour=0, minute=0, second=0, microsecond=0)
        while cursor < window_end:
            d = cursor.date().isoformat()
            accessed.append({"day": d, "count": by_day.get(d, 0)})
            cursor += timedelta(days=1)
            if len(accessed) > _MAX_WINDOW_DAYS + 1:
                break
    else:
        for d in sorted(by_day.keys()):
            accessed.append({"day": d, "count": by_day[d]})
    return accessed


async def _retrieval_totals(
    session: AsyncSession,
    memory_filter: ColumnElement[bool],
    window_start: datetime | None,
    window_end: datetime | None,
) -> tuple[int, int]:
    """Return ``(accesses_in_window, unique_memories_accessed)``."""
    filters: list[ColumnElement[bool]] = [
        MemoryRecall.memory_id == Memory.id,
        memory_filter,
    ]
    if window_start is not None:
        filters.append(MemoryRecall.recalled_at >= window_start)
    if window_end is not None:
        filters.append(MemoryRecall.recalled_at < window_end)
    row = (
        await session.execute(
            select(
                func.count().label("accesses"),
                func.count(func.distinct(MemoryRecall.memory_id)).label("unique_memories"),
            )
            .select_from(MemoryRecall)
            .join(Memory, Memory.id == MemoryRecall.memory_id)
            .where(*filters)
        )
    ).one()
    return int(row.accesses or 0), int(row.unique_memories or 0)


async def _top_accessed(
    session: AsyncSession,
    memory_filter: ColumnElement[bool],
    window_start: datetime | None,
    window_end: datetime | None,
) -> list[dict[str, Any]]:
    filters: list[ColumnElement[bool]] = [
        MemoryRecall.memory_id == Memory.id,
        memory_filter,
    ]
    if window_start is not None:
        filters.append(MemoryRecall.recalled_at >= window_start)
    if window_end is not None:
        filters.append(MemoryRecall.recalled_at < window_end)
    rows = (
        await session.execute(
            select(
                Memory.id,
                Memory.title,
                Memory.kind,
                Memory.entry_type,
                Space.name,
                func.count().label("access_count"),
            )
            .select_from(MemoryRecall)
            .join(Memory, Memory.id == MemoryRecall.memory_id)
            .join(Space, Space.id == Memory.space_id)
            .where(*filters)
            .group_by(Memory.id, Memory.title, Memory.kind, Memory.entry_type, Space.name)
            .order_by(func.count().desc())
            .limit(_TOP_ACCESSED)
        )
    ).all()
    return [
        {
            "memory_id": str(mid),
            "title": title,
            "kind": kind.value,
            "type": entry_type.value if entry_type is not None else None,
            "access_count": int(count),
            "space_name": space_name,
        }
        for mid, title, kind, entry_type, space_name, count in rows
    ]


async def _accesses_by_source(
    session: AsyncSession,
    memory_filter: ColumnElement[bool],
    window_start: datetime | None,
    window_end: datetime | None,
) -> list[dict[str, Any]]:
    filters: list[ColumnElement[bool]] = [
        MemoryRecall.memory_id == Memory.id,
        memory_filter,
    ]
    if window_start is not None:
        filters.append(MemoryRecall.recalled_at >= window_start)
    if window_end is not None:
        filters.append(MemoryRecall.recalled_at < window_end)
    rows = (
        await session.execute(
            select(MemoryRecall.source, func.count())
            .select_from(MemoryRecall)
            .join(Memory, Memory.id == MemoryRecall.memory_id)
            .where(*filters)
            .group_by(MemoryRecall.source)
            .order_by(func.count().desc())
        )
    ).all()
    return [{"source": source, "count": int(count)} for source, count in rows]


async def memory_metrics(
    session: AsyncSession,
    principal: Principal,
    *,
    range_key: str | None = None,
    from_raw: str | None = None,
    to_raw: str | None = None,
    space_scope: str | None = None,
    space_id: uuid.UUID | None = None,
    kind: str | None = None,
    types: Sequence[str] | None = None,
    tags: Sequence[str] | None = None,
    project_id: uuid.UUID | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    """Aggregate storage and retrieval stats visible to the principal.

    Args:
        session: Database session (RLS context already applied).
        principal: Authenticated caller.
        range_key: ``7d`` / ``30d`` / ``90d`` / ``all`` (default ``30d``).
        from_raw: Optional ISO window start (with ``to_raw`` overrides preset).
        to_raw: Optional ISO window end (exclusive-ish upper bound).
        space_scope: Optional ``personal`` or ``group`` when ``space_id`` unset.
        space_id: Optional single space filter.
        kind: Optional ``entry`` or ``guide``.
        types: Optional entry-type / ``guide`` tokens.
        tags: Optional AND tag filter.
        project_id: Optional project filter.
        q: Optional title substring filter.

    Returns:
        Metrics payload for the GUI.

    Raises:
        ValidationError: Invalid range, window, or facet filters.
    """
    now = datetime.now(UTC)
    preset = _parse_range(range_key)
    from_dt = parse_datetime(from_raw, "from")
    to_dt = parse_datetime(to_raw, "to")
    range_label, window_start, window_end = _resolve_window(
        preset, from_dt=from_dt, to_dt=to_dt, now=now
    )

    tag_list = check_tags(list(tags)) if tags else None
    q_clean = q.strip() if q and q.strip() else None

    space_ids = await accessible_space_ids(session, principal, space_id, space_scope=space_scope)
    if not space_ids:
        return _empty_payload(
            range_label, window_start=window_start, window_end=window_end, now=now
        )

    facet_parts = _facet_clauses(
        space_ids=space_ids,
        kind=kind,
        types=types,
        tags=tag_list,
        project_id=project_id,
        q=q_clean,
    )
    base = _and(facet_parts)
    windowed = _window_created_clauses(base, window_start, window_end)

    totals = await _totals(session, base, windowed, now)
    accesses, unique_accessed = await _retrieval_totals(session, base, window_start, window_end)
    totals["accesses_in_window"] = accesses
    totals["unique_memories_accessed"] = unique_accessed

    by_kind = await _by_kind(session, windowed)
    by_type = await _by_type(session, windowed)
    by_space = await _by_space(session, windowed)
    by_project = await _by_project(session, windowed)
    top_tags = await _top_tags(session, windowed)
    top_creators = await _top_creators(session, windowed)
    created_per_day = await _created_per_day(session, base, window_start, window_end)
    accessed_per_day = await _accessed_per_day(session, base, window_start, window_end)
    top_accessed = await _top_accessed(session, base, window_start, window_end)
    accesses_by_source = await _accesses_by_source(session, base, window_start, window_end)

    return {
        "range": range_label,
        "window": {
            "from": window_start.isoformat() if window_start else None,
            "to": window_end.isoformat() if window_end else None,
            "range": range_label,
        },
        "generated_at": now.isoformat(),
        "totals": totals,
        "created_per_day": created_per_day,
        "accessed_per_day": accessed_per_day,
        "by_kind": by_kind,
        "by_type": by_type,
        "by_space": by_space,
        "by_project": by_project,
        "top_tags": top_tags,
        "top_creators": top_creators,
        "top_accessed": top_accessed,
        "accesses_by_source": accesses_by_source,
    }
