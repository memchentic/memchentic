"""Per-user usefulness signals on memories."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import Principal
from memchentic.db.session import commit_with_rls
from memchentic.models.entities import MemoryFeedback
from memchentic.services.errors import NotFoundError, ValidationError
from memchentic.services.memories import open_memory

FeedbackSignal = Literal["useful", "stale", "wrong"]

_FEEDBACK_SIGNALS = frozenset({"useful", "stale", "wrong"})


async def submit_feedback(
    session: AsyncSession,
    principal: Principal,
    memory_id: uuid.UUID,
    signal: str,
) -> None:
    """Record or replace the caller's feedback on one memory.

    Args:
        session: Database session.
        principal: Authenticated caller.
        memory_id: Target memory.
        signal: ``useful``, ``stale``, or ``wrong``.

    Raises:
        ValidationError: When ``signal`` is not allowed.
        NotFoundError: When the memory is missing or not readable.
    """
    if signal not in _FEEDBACK_SIGNALS:
        raise ValidationError(f"signal must be one of useful, stale, wrong; got {signal!r}")
    memory = await open_memory(session, principal, memory_id=memory_id)
    if memory is None:
        raise NotFoundError("Memory not found")
    now = datetime.now(UTC)
    await session.execute(
        pg_insert(MemoryFeedback)
        .values(
            id=uuid.uuid4(),
            memory_id=memory.id,
            space_id=memory.space_id,
            user_id=principal.user_id,
            signal=signal,
            created_at=now,
        )
        .on_conflict_do_update(
            index_elements=["memory_id", "user_id"],
            set_={"signal": signal, "created_at": now},
        )
    )
    await commit_with_rls(session, user_id=principal.user_id, is_admin=principal.is_admin)


async def feedback_scores_for_ids(
    session: AsyncSession,
    memory_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, float]:
    """Aggregate feedback into a normalised score per memory id.

    ``useful`` counts as ``+1``; ``stale`` and ``wrong`` count as ``-1``. The
    returned value is the mean score in ``[-1, 1]``; memories with no feedback
    are omitted.

    Args:
        session: Database session.
        memory_ids: Ids to summarise.

    Returns:
        Mean signal score keyed by memory id.
    """
    if not memory_ids:
        return {}
    score_expr = case(
        (MemoryFeedback.signal == "useful", 1.0),
        else_=-1.0,
    )
    stmt = (
        select(
            MemoryFeedback.memory_id,
            func.avg(score_expr).label("score"),
        )
        .where(MemoryFeedback.memory_id.in_(memory_ids))
        .group_by(MemoryFeedback.memory_id)
    )
    rows = (await session.execute(stmt)).all()
    return {row.memory_id: float(row.score) for row in rows}
