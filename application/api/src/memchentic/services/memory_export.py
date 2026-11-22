"""Streaming export of memories the caller can read."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import Principal
from memchentic.services import memories
from memchentic.services.memory_serialize import memory_to_dict
from memchentic.settings import get_settings


async def export_memories(
    session: AsyncSession,
    principal: Principal,
    *,
    space_id: uuid.UUID | None = None,
    space_scope: str | None = None,
    kind: str | None = None,
    entry_type: str | None = None,
    types: Sequence[str] | None = None,
    include_expired: bool = False,
    tags: list[str] | None = None,
    project_id: uuid.UUID | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Yield full memory dicts using the same filters as ``browse``.

    Args:
        session: Database session.
        principal: Authenticated caller.
        space_id: Optional space filter.
        space_scope: ``personal`` or ``group`` scope filter.
        kind: ``entry`` or ``guide``.
        entry_type: Closed entry type name.
        types: Token list (entry types and/or ``guide``).
        include_expired: Include past-due memories.
        tags: Tag AND filter.
        project_id: Project filter.

    Yields:
        Serialised memory rows (``full=True``).
    """
    page = get_settings().browse_default_limit
    offset = 0
    while True:
        rows = await memories.browse(
            session,
            principal,
            space_id=space_id,
            space_scope=space_scope,
            kind=kind,
            entry_type=entry_type,
            types=types,
            limit=page,
            offset=offset,
            include_expired=include_expired,
            tags=tags,
            project_id=project_id,
        )
        if not rows:
            break
        for memory in rows:
            yield memory_to_dict(memory, full=True)
        if len(rows) < page:
            break
        offset += len(rows)
