"""ACL-scoped memory corpus aggregates for the Metrics GUI."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from sqlalchemy import ColumnElement, String, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import Principal
from memchentic.models.entities import Memory, MemoryKind, Space, User
from memchentic.services.errors import ValidationError
from memchentic.services.memories import accessible_space_ids

MetricRange = Literal["7d", "30d", "90d", "all"]

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


def _parse_range(raw: str | None) -> MetricRange:
    """Normalize a range query token.

    Args:
        raw: Client ``range`` value.

    Returns:
        A supported range token (default ``30d``).

    Raises:
        ValidationError: When ``raw`` is set but not a known token.
    """
    if raw is None or raw == "":
        return "30d"
    if raw not in _RANGE_DAYS:
        raise ValidationError(f"Invalid range {raw!r}; expected 7d, 30d, 90d, or all")
    return cast(MetricRange, raw)


def _empty_payload(range_token: MetricRange) -> dict[str, Any]:
    """Return a zeroed metrics payload for an empty accessible corpus."""
    return {
        "range": range_token,
        "generated_at": datetime.now(UTC).isoformat(),
        "totals": {
            "memories": 0,
            "entries": 0,
            "guides": 0,
            "expiring_soon": 0,
            "spaces": 0,
        },
        "created_per_day": [],
        "by_kind": [],
        "by_type": [],
        "by_space": [],
        "top_tags": [],
        "top_creators": [],
    }


async def memory_metrics(
    session: AsyncSession,
    principal: Principal,
    *,
    range_key: str | None = None,
    space_scope: str | None = None,
    space_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Aggregate memory corpus stats visible to the principal.

    KPI totals and categorical breakdowns count all ACL-visible rows.
    ``created_per_day`` is limited to the selected time window.

    Args:
        session: Database session (RLS context already applied).
        principal: Authenticated caller.
        range_key: ``7d`` / ``30d`` / ``90d`` / ``all`` (default ``30d``).
        space_scope: Optional ``personal`` or ``group`` when ``space_id`` unset.
        space_id: Optional single space filter.

    Returns:
        Metrics payload for the GUI.

    Raises:
        ValidationError: Invalid ``range_key`` or ``space_scope``.
    """
    range_token = _parse_range(range_key)
    space_ids = await accessible_space_ids(session, principal, space_id, space_scope=space_scope)
    if not space_ids:
        return _empty_payload(range_token)

    base = Memory.space_id.in_(space_ids)
    now = datetime.now(UTC)
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
            )
            .select_from(Memory)
            .where(base)
        )
    ).one()
    total = totals_row.total
    entries = totals_row.entries
    guides = totals_row.guides
    expiring = totals_row.expiring
    spaces_used = totals_row.spaces_used

    # Entry and guide are the only two kinds, so by_kind follows from the
    # totals. Order: count descending, as before.
    kind_counts = [(MemoryKind.entry, int(entries)), (MemoryKind.guide, int(guides))]
    by_kind = [
        {"kind": kind.value, "count": count}
        for kind, count in sorted(kind_counts, key=lambda pair: pair[1], reverse=True)
        if count
    ]

    type_rows = (
        await session.execute(
            select(Memory.entry_type, func.count())
            .where(base, Memory.kind == MemoryKind.entry, Memory.entry_type.is_not(None))
            .group_by(Memory.entry_type)
            .order_by(func.count().desc())
        )
    ).all()
    by_type = [{"type": t.value, "count": int(c)} for t, c in type_rows if t is not None]

    space_rows = (
        await session.execute(
            select(Memory.space_id, Space.name, func.count())
            .join(Space, Space.id == Memory.space_id)
            .where(base)
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

    tag_sq = (
        select(func.unnest(Memory.tags, type_=String).label("tag"))
        .where(base, func.cardinality(Memory.tags) > 0)
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
    top_tags = [{"tag": tag, "count": int(count)} for tag, count in tag_rows]

    creator_rows = (
        await session.execute(
            select(
                Memory.created_by_user_id,
                User.email,
                User.display_name,
                func.count(),
            )
            .outerjoin(User, User.id == Memory.created_by_user_id)
            .where(base, Memory.created_by_user_id.is_not(None))
            .group_by(Memory.created_by_user_id, User.email, User.display_name)
            .order_by(func.count().desc())
            .limit(_TOP_CREATORS)
        )
    ).all()
    top_creators = [
        {
            "user_id": str(uid),
            "email": email,
            "display_name": display_name,
            "count": int(c),
        }
        for uid, email, display_name, c in creator_rows
    ]

    days = _RANGE_DAYS[range_token]
    # Three-argument date_trunc truncates in the named zone, so the day
    # bucket does not depend on the database session time zone. The zone
    # is a SQL literal so that SELECT and GROUP BY render the same text.
    day_expr = func.date_trunc("day", Memory.created_at, literal_column("'UTC'"))
    day_filters: list[ColumnElement[bool]] = [base]
    window_start: datetime | None = None
    if days is not None:
        window_start = (now - timedelta(days=days - 1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        day_filters.append(Memory.created_at >= window_start)

    day_rows = (
        await session.execute(
            select(
                day_expr.label("day"),
                Memory.kind,
                func.count(),
            )
            .where(*day_filters)
            .group_by(day_expr, Memory.kind)
            .order_by(day_expr)
        )
    ).all()

    by_day: dict[str, dict[str, int]] = {}
    for day_val, kind, count in day_rows:
        if day_val is None:
            continue
        # date_trunc() has no declared Python type; asyncpg returns a datetime.
        if hasattr(day_val, "astimezone"):
            key = day_val.astimezone(UTC).date().isoformat()
        else:
            key = str(day_val)[:10]
        bucket = by_day.setdefault(key, {"entries": 0, "guides": 0})
        kind_key = kind.value
        if kind_key == MemoryKind.guide.value:
            bucket["guides"] += int(count)
        else:
            bucket["entries"] += int(count)

    created_per_day: list[dict[str, Any]] = []
    if days is not None and window_start is not None:
        for offset in range(days):
            d = (window_start + timedelta(days=offset)).date().isoformat()
            bucket = by_day.get(d, {"entries": 0, "guides": 0})
            created_per_day.append(
                {"day": d, "entries": bucket["entries"], "guides": bucket["guides"]}
            )
    else:
        for d in sorted(by_day.keys()):
            bucket = by_day[d]
            created_per_day.append(
                {"day": d, "entries": bucket["entries"], "guides": bucket["guides"]}
            )

    return {
        "range": range_token,
        "generated_at": now.isoformat(),
        "totals": {
            "memories": int(total or 0),
            "entries": int(entries or 0),
            "guides": int(guides or 0),
            "expiring_soon": int(expiring or 0),
            "spaces": int(spaces_used or 0),
        },
        "created_per_day": created_per_day,
        "by_kind": by_kind,
        "by_type": by_type,
        "by_space": by_space,
        "top_tags": top_tags,
        "top_creators": top_creators,
    }
