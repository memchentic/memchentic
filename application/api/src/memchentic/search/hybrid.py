from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.embeddings import get_embedder
from memchentic.models.entities import EntryType, Memory, MemoryKind
from memchentic.settings import get_settings


def memory_type_clause(tokens: Sequence[str] | None) -> ColumnElement[bool] | None:
    """Build an OR filter for entry types and/or guides.

    Args:
        tokens: Entry-type values and/or ``guide``. Empty/None means no filter.

    Returns:
        SQLAlchemy clause, or None when unrestricted.
    """
    if not tokens:
        return None
    include_guide = "guide" in tokens
    entry_names = [t for t in tokens if t != "guide"]
    entry_enums: list[EntryType] = []
    for name in entry_names:
        try:
            entry_enums.append(EntryType(name))
        except ValueError as exc:
            raise ValueError(f"unknown memory type: {name}") from exc
    clauses: list[ColumnElement[bool]] = []
    if include_guide:
        clauses.append(Memory.kind == MemoryKind.guide)
    if entry_enums:
        clauses.append(
            and_(Memory.kind == MemoryKind.entry, Memory.entry_type.in_(entry_enums))
        )
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return or_(*clauses)


@dataclass
class Digest:
    id: str
    title: str
    score: float
    snippet: str
    type: str | None
    tags: list[str]
    space: str
    kind: str
    created_by: str | None = None


def _snippet(body: str, limit: int = 180) -> str:
    body = (body or "").strip().replace("\n", " ")
    if len(body) <= limit:
        return body
    return body[: limit - 1] + "…"


def rrf_fuse(
    ranked_lists: list[list[uuid.UUID]],
    *,
    k: int,
) -> list[tuple[uuid.UUID, float]]:
    scores: dict[uuid.UUID, float] = {}
    for ranked in ranked_lists:
        for rank, item_id in enumerate(ranked, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


async def hybrid_search(
    session: AsyncSession,
    *,
    query: str,
    space_ids: list[uuid.UUID] | None = None,
    kind: MemoryKind | None = None,
    entry_type: str | None = None,
    type_tokens: Sequence[str] | None = None,
    tags: list[str] | None = None,
    tag_mode: str = "and",
    path_hint: str | None = None,
    repo_hint: str | None = None,
    limit: int | None = None,
) -> list[Digest]:
    settings = get_settings()
    embedder = get_embedder()
    qvec = await embedder.aembed(query)
    limit = settings.resolve_search_limit(limit)
    fetch_n = max(limit * 3, 30)

    base_filters = []
    if space_ids is not None:
        if not space_ids:
            return []
        base_filters.append(Memory.space_id.in_(space_ids))
    if type_tokens is not None:
        clause = memory_type_clause(type_tokens)
        if clause is not None:
            base_filters.append(clause)
    else:
        if kind is not None:
            base_filters.append(Memory.kind == kind)
        if entry_type:
            base_filters.append(Memory.entry_type == entry_type)
    if path_hint:
        base_filters.append(Memory.path_hint.ilike(f"%{path_hint}%"))
    if repo_hint:
        base_filters.append(Memory.repo_hint.ilike(f"%{repo_hint}%"))
    if tags:
        if tag_mode == "or":
            base_filters.append(Memory.tags.overlap(tags))
        else:
            base_filters.append(Memory.tags.contains(tags))

    # Vector branch
    vec_stmt = select(Memory.id).where(Memory.embedding.is_not(None), *base_filters)
    vec_stmt = vec_stmt.order_by(Memory.embedding.cosine_distance(qvec)).limit(fetch_n)
    vec_ids = [row[0] for row in (await session.execute(vec_stmt)).all()]

    # FTS branch
    fts_ids: list[uuid.UUID] = []
    if query.strip():
        fts_stmt = (
            select(Memory.id)
            .where(*base_filters)
            .where(Memory.search_tsv.op("@@")(func.websearch_to_tsquery("english", query)))
            .order_by(func.ts_rank_cd(Memory.search_tsv, func.websearch_to_tsquery("english", query)).desc())
            .limit(fetch_n)
        )
        fts_ids = [row[0] for row in (await session.execute(fts_stmt)).all()]
        # Fallback ILIKE if FTS empty
        if not fts_ids:
            like = f"%{query.strip()}%"
            like_stmt = (
                select(Memory.id)
                .where(*base_filters)
                .where(or_(Memory.title.ilike(like), Memory.body.ilike(like)))
                .order_by(Memory.updated_at.desc())
                .limit(fetch_n)
            )
            fts_ids = [row[0] for row in (await session.execute(like_stmt)).all()]

    fused = rrf_fuse([vec_ids, fts_ids], k=settings.rrf_k)[:limit]
    if not fused:
        return []

    ids = [item_id for item_id, _ in fused]
    score_map = {item_id: score for item_id, score in fused}
    rows = (
        await session.execute(select(Memory).where(Memory.id.in_(ids)))
    ).scalars().all()
    by_id = {m.id: m for m in rows}
    digests: list[Digest] = []
    for item_id in ids:
        m = by_id.get(item_id)
        if not m:
            continue
        digests.append(
            Digest(
                id=str(m.id),
                title=m.title,
                score=round(score_map[item_id], 6),
                snippet=_snippet(m.body),
                type=m.entry_type.value if m.entry_type else None,
                tags=list(m.tags or []),
                space=str(m.space_id),
                kind=m.kind.value,
                created_by=str(m.created_by_user_id) if m.created_by_user_id else None,
            )
        )
    return digests
