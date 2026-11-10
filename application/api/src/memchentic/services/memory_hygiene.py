"""Near-duplicate detection, dedup locking and the overlap report."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import Principal
from memchentic.embeddings import get_embedder
from memchentic.models.entities import Memory, MemoryKind
from memchentic.search.hybrid import active_memory_clause, prepare_filtered_vector_scan
from memchentic.services.errors import ValidationError
from memchentic.services.memory_serialize import CONTENT_TRUST
from memchentic.services.memory_ttl import active_select
from memchentic.services.spaces import accessible_space_ids
from memchentic.settings import get_settings


def dedup_lock_key(space_id: uuid.UUID, kind: MemoryKind) -> str:
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
                active_select(space_ids, include_expired)
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
