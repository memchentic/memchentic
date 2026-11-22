"""Near-duplicate detection, dedup locking and the overlap report."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from memchentic.auth.principal import Principal
from memchentic.embeddings import get_embedder
from memchentic.models.entities import Memory, MemoryKind
from memchentic.search.hybrid import active_memory_clause, prepare_filtered_vector_scan
from memchentic.services.errors import NotFoundError, ValidationError
from memchentic.services.memory_serialize import CONTENT_TRUST
from memchentic.services.memory_ttl import active_select
from memchentic.services.spaces import accessible_space_ids, get_readable_space
from memchentic.settings import get_settings


async def resolve_supersedes_target(
    session: AsyncSession,
    principal: Principal,
    *,
    supersedes_id: uuid.UUID,
    space_id: uuid.UUID,
    kind: MemoryKind,
    self_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Validate a declared supersession target and return its id.

    The target must be readable, in the same space, the same kind, and not
    the writer memory itself.

    Args:
        session: Database session.
        principal: Authenticated caller.
        supersedes_id: Id of the memory being replaced.
        space_id: Space of the writer memory.
        kind: Kind of the writer memory.
        self_id: Id of the writer when revising an existing row.

    Returns:
        ``supersedes_id`` when valid.

    Raises:
        ValidationError: Self-reference, wrong space, or wrong kind.
        NotFoundError: Missing or unreadable target.
    """
    if self_id is not None and supersedes_id == self_id:
        raise ValidationError("supersedes_id cannot refer to the same memory")
    result = await session.execute(select(Memory).where(Memory.id == supersedes_id))
    target = result.scalar_one_or_none()
    if target is None or not await get_readable_space(session, principal, target.space_id):
        raise NotFoundError("Superseded memory not found")
    if target.space_id != space_id:
        raise ValidationError("supersedes_id must be in the same space")
    if target.kind != kind:
        raise ValidationError("supersedes_id must be the same kind")
    return target.id


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


_OVERLAP_NOTE = (
    "Hygiene report: resolve conflicts with resolve_conflict (forgets the superseded "
    "memory) and near-duplicates with resolve_near_duplicate (forgets the drop id), "
    "or revise/forget by hand. conflicts lists declared supersedes links where both "
    "memories are still visible."
)

_AUTOMATION_PASS_CAP = 50


async def resolve_conflict(
    session: AsyncSession,
    principal: Principal,
    *,
    replacement_id: uuid.UUID,
    superseded_id: uuid.UUID,
) -> dict[str, Any]:
    """Accept a declared supersession by forgetting the superseded memory.

    Verifies that ``replacement_id`` still points at ``superseded_id`` via
    ``supersedes_id``, then deletes the superseded row under the same ACL as
    ``forget``.

    Args:
        session: Database session.
        principal: Authenticated caller.
        replacement_id: Memory that declares the replacement (``a``).
        superseded_id: Memory being replaced (``b``).

    Returns:
        ``{"ok": True, "forgotten": "<superseded_id>"}``.

    Raises:
        NotFoundError: When either memory is missing or unreadable.
        ValidationError: When the live supersedes link does not match.
        ForbiddenError: When the caller may not forget the superseded memory.
    """
    from memchentic.services.memories import forget, open_memory

    writer = await open_memory(session, principal, memory_id=replacement_id)
    if writer is None:
        raise NotFoundError("Replacement memory not found")
    if writer.supersedes_id != superseded_id:
        raise ValidationError("a does not currently supersede b")
    target = await open_memory(session, principal, memory_id=superseded_id)
    if target is None:
        raise NotFoundError("Superseded memory not found")
    await forget(session, principal, superseded_id)
    return {"ok": True, "forgotten": str(superseded_id)}


def _are_near_duplicates(left: Memory, right: Memory) -> bool:
    """True when two live memories share a title or meet the cosine threshold."""
    if left.space_id != right.space_id or left.kind != right.kind:
        return False
    if left.title.strip().lower() == right.title.strip().lower():
        return True
    if left.embedding is None or right.embedding is None:
        return False
    a = np.asarray(left.embedding, dtype=np.float32)
    b = np.asarray(right.embedding, dtype=np.float32)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return False
    similarity = float(np.dot(a, b) / denom)
    return similarity >= get_settings().near_dup_threshold


async def resolve_near_duplicate(
    session: AsyncSession,
    principal: Principal,
    *,
    keep_id: uuid.UUID,
    drop_id: uuid.UUID,
) -> dict[str, Any]:
    """Keep one near-duplicate and forget the other.

    Both memories must be readable, the same space and kind, and either share
    a title or meet the near-duplicate similarity threshold.

    Args:
        session: Database session.
        principal: Authenticated caller.
        keep_id: Memory to keep.
        drop_id: Memory to delete.

    Returns:
        ``{"ok": True, "forgotten": "<drop_id>"}``.

    Raises:
        NotFoundError: When either memory is missing or unreadable.
        ValidationError: When the ids are equal or not a near-duplicate pair.
        ForbiddenError: When the caller may not forget ``drop_id``.
    """
    from memchentic.services.memories import forget, open_memory

    if keep_id == drop_id:
        raise ValidationError("keep and drop must be different memories")
    keep = await open_memory(session, principal, memory_id=keep_id)
    if keep is None:
        raise NotFoundError("Keep memory not found")
    drop = await open_memory(session, principal, memory_id=drop_id)
    if drop is None:
        raise NotFoundError("Drop memory not found")
    if not _are_near_duplicates(keep, drop):
        raise ValidationError("keep and drop are not a near-duplicate pair")
    await forget(session, principal, drop_id)
    return {"ok": True, "forgotten": str(drop_id)}


def _older_memory_id(left: Memory, right: Memory) -> uuid.UUID:
    """Return the id of the older memory by ``updated_at``, then by id."""
    if left.updated_at != right.updated_at:
        return left.id if left.updated_at < right.updated_at else right.id
    return left.id if left.id < right.id else right.id


async def run_hygiene_automation(session: AsyncSession) -> dict[str, int]:
    """Apply enabled Automation settings: forget superseded / older near-dups.

    Loads the admin user as the acting principal. Caps deletes per pass.
    Safe to call when both toggles are off (returns zeros). Re-binds RLS
    after each forget because ``commit_with_rls`` clears the context.

    Args:
        session: Database session.

    Returns:
        Counts: ``conflicts_forgotten``, ``near_dups_forgotten``.
    """
    from memchentic.db.session import set_rls_context
    from memchentic.services import instance_settings as instance_settings_service
    from memchentic.services.memories import open_memory
    from memchentic.services.users import ensure_admin_user

    flags = await instance_settings_service.get_automation_settings(session)
    result = {"conflicts_forgotten": 0, "near_dups_forgotten": 0}
    if not flags["resolve_supersedes"] and not flags["resolve_near_dups"]:
        return result

    settings = get_settings()
    admin = await ensure_admin_user(session, settings)
    principal = Principal(
        user_id=admin.id,
        is_admin=True,
        email=admin.email,
        display_name=admin.display_name,
    )
    await set_rls_context(session, user_id=principal.user_id, is_admin=True)
    report = await overlap_report(session, principal, limit=_AUTOMATION_PASS_CAP)
    remaining = _AUTOMATION_PASS_CAP

    if flags["resolve_supersedes"]:
        for pair in report["conflicts"]:
            if remaining <= 0:
                break
            await set_rls_context(session, user_id=principal.user_id, is_admin=True)
            try:
                await resolve_conflict(
                    session,
                    principal,
                    replacement_id=uuid.UUID(pair["a"]),
                    superseded_id=uuid.UUID(pair["b"]),
                )
            except (NotFoundError, ValidationError):
                continue
            result["conflicts_forgotten"] += 1
            remaining -= 1

    if flags["resolve_near_dups"] and remaining > 0:
        for pair in report["near_duplicates"]:
            if remaining <= 0:
                break
            await set_rls_context(session, user_id=principal.user_id, is_admin=True)
            a_id = uuid.UUID(pair["a"])
            b_id = uuid.UUID(pair["b"])
            left = await open_memory(session, principal, memory_id=a_id)
            right = await open_memory(session, principal, memory_id=b_id)
            if left is None or right is None:
                continue
            drop_id = _older_memory_id(left, right)
            keep_id = a_id if drop_id == b_id else b_id
            try:
                await resolve_near_duplicate(session, principal, keep_id=keep_id, drop_id=drop_id)
            except (NotFoundError, ValidationError):
                continue
            result["near_dups_forgotten"] += 1
            remaining -= 1

    return result


async def _scan_rows(
    session: AsyncSession,
    space_ids: list[uuid.UUID],
    include_expired: bool,
    scan_limit: int,
) -> tuple[list[Memory], bool]:
    """Load the newest embedded rows to compare and whether the scan was cut off."""
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
    return rows, truncated


def _overlap_payload(
    near_duplicates: list[dict[str, Any]],
    expiring: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    scanned: int,
    truncated: bool,
    threshold: float,
) -> dict[str, Any]:
    """Build the overlap report body in its fixed key order."""
    return {
        "near_duplicates": near_duplicates,
        "expiring": expiring,
        "conflicts": conflicts,
        "scanned": scanned,
        "truncated": truncated,
        "note": _OVERLAP_NOTE,
        "near_dup_threshold": threshold,
        "content_trust": CONTENT_TRUST,
    }


def _near_duplicate_pairs(rows: list[Memory], limit: int, threshold: float) -> list[dict[str, Any]]:
    """Pair rows that share a title or exceed the cosine threshold, up to ``limit``."""
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
    return near_duplicates


async def _expiring_soon(
    session: AsyncSession,
    space_ids: list[uuid.UUID],
    limit: int,
    expiring_within_days: int | None,
) -> list[dict[str, Any]]:
    """List the memories that expire next in the given spaces."""
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
    return expiring


async def _supersedes_conflicts(
    session: AsyncSession,
    space_ids: list[uuid.UUID],
    limit: int,
    include_expired: bool,
) -> list[dict[str, Any]]:
    """List declared supersession pairs where both ends are still visible."""
    target = aliased(Memory)
    stmt = (
        select(Memory)
        .join(target, Memory.supersedes_id == target.id)
        .where(
            Memory.space_id.in_(space_ids),
            Memory.supersedes_id.is_not(None),
            target.space_id == Memory.space_id,
            target.kind == Memory.kind,
        )
        .order_by(Memory.updated_at.desc(), Memory.id)
        .limit(limit)
    )
    if not include_expired:
        now = datetime.now(UTC)
        live = or_(Memory.expires_at.is_(None), Memory.expires_at > now)
        target_live = or_(target.expires_at.is_(None), target.expires_at > now)
        stmt = stmt.where(live, target_live)
    writers = list((await session.execute(stmt)).scalars())
    return [
        {
            "a": str(writer.id),
            "b": str(writer.supersedes_id),
            "title": writer.title,
            "reason": "supersedes",
        }
        for writer in writers
    ]


async def overlap_report(
    session: AsyncSession,
    principal: Principal,
    *,
    space_id: uuid.UUID | None = None,
    space_scope: str | None = None,
    limit: int | None = None,
    include_expired: bool = False,
    expiring_within_days: int | None = None,
) -> dict[str, Any]:
    """Report duplicate candidates, conflicts and soon-to-expire memories.

    Args:
        session: Database session.
        principal: Authenticated caller.
        space_id: Limit the report to one space.
        space_scope: Optional ``personal`` or ``group`` filter when ``space_id``
            is omitted.
        limit: Most pairs and most expiring memories to return.
        include_expired: Also compare past-due memories.
        expiring_within_days: List only memories that expire within this many
            days; ``None`` lists the next ``limit`` expiries.

    Returns:
        ``near_duplicates`` (pairs), ``expiring``, ``conflicts`` (declared
        supersedes links), ``scanned`` (number of memories compared for
        near-dups) and ``truncated`` (True when more memories exist than
        ``OVERLAP_SCAN_LIMIT``; the oldest were not compared for near-dups).
    """
    settings = get_settings()
    if expiring_within_days is not None and expiring_within_days < 0:
        raise ValidationError("expiring_within_days must be >= 0")
    limit = settings.resolve_search_limit(limit)
    space_ids = await accessible_space_ids(session, principal, space_id, space_scope=space_scope)
    if not space_ids:
        return _overlap_payload([], [], [], 0, False, settings.near_dup_threshold)

    rows, truncated = await _scan_rows(
        session, space_ids, include_expired, settings.overlap_scan_limit
    )

    near_duplicates = _near_duplicate_pairs(rows, limit, settings.near_dup_threshold)
    expiring = await _expiring_soon(session, space_ids, limit, expiring_within_days)
    conflicts = await _supersedes_conflicts(session, space_ids, limit, include_expired)

    return _overlap_payload(
        near_duplicates,
        expiring,
        conflicts,
        len(rows),
        truncated,
        settings.near_dup_threshold,
    )
