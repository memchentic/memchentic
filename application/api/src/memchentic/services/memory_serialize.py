"""Memory row and digest serialisation for REST and MCP responses."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.models.entities import Memory, Space, User
from memchentic.search.hybrid import Digest, make_snippet


def memory_to_dict(m: Memory, *, full: bool = False) -> dict[str, Any]:
    """Serialise a memory for REST and MCP responses.

    Args:
        m: Memory row.
        full: Include the full-only fields (for example ``provenance``).

    Returns:
        A JSON-ready dict.
    """
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


def digest_to_dict(d: Digest) -> dict[str, Any]:
    """Serialise a search digest for REST and MCP responses."""
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


def digest_from_memory(m: Memory, *, score: float = 1.0) -> Digest:
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
