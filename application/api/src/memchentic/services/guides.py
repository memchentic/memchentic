"""Guides: upserting reusable advice and guide-only search."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import Principal
from memchentic.db.session import advisory_xact_lock
from memchentic.embeddings import get_embedder
from memchentic.models.entities import Memory, MemoryKind
from memchentic.search.hybrid import GUIDE_CONFIDENCE_MAX, Digest, hybrid_search
from memchentic.services.memories import publish_and_commit
from memchentic.services.memory_hygiene import dedup_lock_key, find_near_duplicate
from memchentic.services.memory_ttl import (
    check_lifetime_args,
    resolve_expiry,
    slide_expiry,
    touch_digests,
)
from memchentic.services.spaces import accessible_space_ids
from memchentic.services.validation import (
    check_body,
    check_offset,
    check_query,
    check_tags,
    check_title,
)
from memchentic.services.write_target import Visibility, resolve_write_space


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
    await advisory_xact_lock(session, dedup_lock_key(space.id, MemoryKind.guide))
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
        return await publish_and_commit(session, principal, dup, "updated")

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
    return await publish_and_commit(session, principal, memory, "created")


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
    """Hybrid search over guides in readable spaces.

    Returns:
        Digests, best first.
    """
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
    return await touch_digests(session, principal, digests)
