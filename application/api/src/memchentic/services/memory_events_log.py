"""Durable append-only log of memory mutation events."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import Principal
from memchentic.events.memory_bus import MemoryEvent
from memchentic.models.entities import MemoryEventLog
from memchentic.services.spaces import accessible_space_ids
from memchentic.services.validation import check_offset
from memchentic.settings import get_settings

_log = logging.getLogger(__name__)


async def append_memory_event(session: AsyncSession, event: MemoryEvent) -> None:
    """Insert a log row from ``event`` in the caller's open transaction.

    Best-effort: a missing space (synthetic notify / race) must not abort
    the caller's NOTIFY or mutation commit. Uses a nested savepoint.

    Args:
        session: Database session.
        event: Compact mutation payload (same shape as NOTIFY / SSE).
    """
    payload = event.to_dict()
    memory_id: uuid.UUID | None = None
    try:
        memory_id = uuid.UUID(event.id)
    except ValueError:
        memory_id = None
    try:
        async with session.begin_nested():
            session.add(
                MemoryEventLog(
                    memory_id=memory_id,
                    space_id=uuid.UUID(event.space_id),
                    action=event.action,
                    payload=payload,
                )
            )
            await session.flush()
    except IntegrityError:
        _log.debug(
            "skip durable memory_events row for space=%s memory=%s",
            event.space_id,
            event.id,
            exc_info=True,
        )


def _row_to_dict(row: MemoryEventLog) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "memory_id": str(row.memory_id) if row.memory_id else None,
        "space_id": str(row.space_id),
        "action": row.action,
        "payload": row.payload or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def list_memory_event_log(
    session: AsyncSession,
    principal: Principal,
    *,
    space_id: uuid.UUID | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List mutation log rows for readable spaces, newest first.

    Args:
        session: Database session.
        principal: Authenticated caller.
        space_id: Optional filter to one readable space.
        limit: Page size.
        offset: Skip rows.

    Returns:
        Event log dicts with embedded ``payload``.
    """
    offset = check_offset(offset)
    page = get_settings().resolve_browse_limit(limit)
    space_ids = await accessible_space_ids(session, principal, space_id)
    if not space_ids:
        return []
    stmt = (
        select(MemoryEventLog)
        .where(MemoryEventLog.space_id.in_(space_ids))
        .order_by(MemoryEventLog.created_at.desc(), MemoryEventLog.id.desc())
        .limit(page)
        .offset(offset)
    )
    rows = list((await session.execute(stmt)).scalars())
    return [_row_to_dict(row) for row in rows]
