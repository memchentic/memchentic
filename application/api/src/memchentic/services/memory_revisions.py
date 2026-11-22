"""Revision history: snapshots taken before memory mutations."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import Principal
from memchentic.models.entities import Memory, MemoryRevision
from memchentic.services.errors import NotFoundError
from memchentic.services.memories import open_memory
from memchentic.services.validation import check_offset
from memchentic.settings import get_settings


def snapshot_fields(memory: Memory) -> dict[str, Any]:
    """Return a JSON-serialisable snapshot of editable memory fields.

    Args:
        memory: Row about to be mutated.

    Returns:
        Field values at the current revision.
    """
    return {
        "title": memory.title,
        "body": memory.body,
        "kind": memory.kind.value,
        "type": memory.entry_type.value if memory.entry_type else None,
        "tags": list(memory.tags or []),
        "path_hint": memory.path_hint,
        "repo_hint": memory.repo_hint,
        "confidence": memory.confidence,
        "expires_at": memory.expires_at.isoformat() if memory.expires_at else None,
        "project_id": str(memory.project_id) if memory.project_id else None,
        "supersedes_id": str(memory.supersedes_id) if memory.supersedes_id else None,
    }


def _revision_to_dict(rev: MemoryRevision) -> dict[str, Any]:
    return {
        "id": str(rev.id),
        "memory_id": str(rev.memory_id),
        "space_id": str(rev.space_id),
        "revised_by": str(rev.revised_by_user_id) if rev.revised_by_user_id else None,
        "revised_at": rev.revised_at.isoformat() if rev.revised_at else None,
        "source": rev.source,
        "changed_fields": list(rev.changed_fields or []),
        "snapshot": rev.snapshot or {},
    }


async def record_revision(
    session: AsyncSession,
    memory: Memory,
    principal: Principal,
    source: str,
    changed_fields: list[str],
) -> None:
    """Insert a revision row capturing ``memory`` before it is updated.

    Callers must flush or commit in the same transaction as the mutation.

    Args:
        session: Database session.
        memory: Memory row about to change.
        principal: Authenticated caller.
        source: Short label for the write path (``rest``, ``mcp``, ...).
        changed_fields: Names of fields the mutation will touch.
    """
    session.add(
        MemoryRevision(
            memory_id=memory.id,
            space_id=memory.space_id,
            revised_by_user_id=principal.user_id,
            source=source,
            changed_fields=list(changed_fields),
            snapshot=snapshot_fields(memory),
        )
    )


async def list_history(
    session: AsyncSession,
    principal: Principal,
    memory_id: uuid.UUID,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """List revision snapshots for one memory, newest first.

    Args:
        session: Database session.
        principal: Authenticated caller.
        memory_id: Target memory.
        limit: Page size; defaults to ``BROWSE_DEFAULT_LIMIT``.
        offset: Row skip for pagination.

    Returns:
        Revision dicts with ``snapshot`` payloads.

    Raises:
        NotFoundError: When the memory is missing or not readable.
    """
    memory = await open_memory(session, principal, memory_id=memory_id)
    if memory is None:
        raise NotFoundError("Memory not found")
    offset = check_offset(offset)
    page = get_settings().resolve_browse_limit(limit)
    stmt = (
        select(MemoryRevision)
        .where(MemoryRevision.memory_id == memory_id)
        .order_by(MemoryRevision.revised_at.desc(), MemoryRevision.id.desc())
        .limit(page)
        .offset(offset)
    )
    rows = list((await session.execute(stmt)).scalars())
    return [_revision_to_dict(rev) for rev in rows]
