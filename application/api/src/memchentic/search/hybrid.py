from __future__ import annotations

import math
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from memchentic.embeddings import get_embedder
from memchentic.models.entities import EntryType, Memory, MemoryKind
from memchentic.services.errors import ValidationError
from memchentic.settings import get_settings


def escape_ilike(value: str) -> str:
    """Escape ``\\``, ``%``, and ``_`` for SQL ``ILIKE … ESCAPE '\\'`` patterns.

    Args:
        value: Raw user fragment embedded between wildcards.

    Returns:
        Escaped string safe to wrap as ``%{value}%``.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


_WORD = re.compile(r"\w+")
# Longest query for which the substring (ILIKE) fallback runs. The fallback
# is a sequential scan; it is useful for identifiers and parts of words.
SUBSTRING_FALLBACK_MAX_CHARS = 64
# Most distinct terms sent to the any-term fallback.
ANY_TERM_MAX_TERMS = 32


def any_term_query(query: str) -> str:
    """Rewrite a query so that ``websearch_to_tsquery`` matches any of its terms.

    ``websearch_to_tsquery`` joins unquoted terms with AND and reads the word
    ``or`` as OR. Punctuation is dropped so that quotes and ``-`` cannot
    change the meaning.

    Args:
        query: Raw user query.

    Returns:
        ``"a or b or c"`` with at most ``ANY_TERM_MAX_TERMS`` distinct terms,
        or an empty string when the query has no word characters.
    """
    terms: list[str] = []
    for word in _WORD.findall(query.lower()):
        if word != "or" and word not in terms:
            terms.append(word)
        if len(terms) >= ANY_TERM_MAX_TERMS:
            break
    return " or ".join(terms)


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
            raise ValidationError(f"unknown memory type: {name}") from exc
    clauses: list[ColumnElement[bool]] = []
    if include_guide:
        clauses.append(Memory.kind == MemoryKind.guide)
    if entry_enums:
        clauses.append(and_(Memory.kind == MemoryKind.entry, Memory.entry_type.in_(entry_enums)))
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return or_(*clauses)


def active_memory_clause(
    *, include_expired: bool, now: datetime | None = None
) -> ColumnElement[bool] | None:
    """Hide past-due memories from recall unless the caller asks for them.

    Args:
        include_expired: When True, apply no expiry filter.
        now: Reference time; defaults to the current UTC time.

    Returns:
        Clause keeping rows with no expiry or a future expiry, or None.
    """
    if include_expired:
        return None
    ref = now or datetime.now(UTC)
    return or_(Memory.expires_at.is_(None), Memory.expires_at > ref)


# Upper bound for guide confidence, in storage and in ranking. Repeating
# guide_write must not raise a guide's rank without limit.
GUIDE_CONFIDENCE_MAX = 5.0


def rerank_score(
    memory: Memory,
    base: float,
    *,
    now: datetime,
    path_hint: str | None = None,
) -> float:
    """Adjust a fused RRF score for recency, path, guide confidence, and usage.

    Recency is a bounded boost, not a decay. The factor is
    ``1 + SEARCH_RECENCY_WEIGHT / (1 + age_days / SEARCH_RECENCY_HALF_LIFE_DAYS)``.
    It is ``1 + SEARCH_RECENCY_WEIGHT`` for a memory edited just now and
    approaches 1 for a very old one, so age alone can change a score by at
    most that ratio.

    Args:
        memory: Loaded memory row.
        base: Reciprocal-rank-fusion score.
        now: Reference time for age.
        path_hint: Caller path filter; rows tagged with a matching ``path_hint``
            outrank body-only matches.

    Returns:
        Re-ranked score.
    """
    settings = get_settings()
    score = base
    updated = memory.updated_at or memory.created_at
    if updated is not None:
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        age_days = max(0.0, (now - updated).total_seconds() / 86400.0)
        freshness = 1.0 / (1.0 + age_days / settings.search_recency_half_life_days)
        score *= 1.0 + settings.search_recency_weight * freshness
    if path_hint and memory.path_hint and path_hint.casefold() in memory.path_hint.casefold():
        score *= 1.0 + settings.search_path_boost
    if memory.kind == MemoryKind.guide:
        confidence = min(float(memory.confidence or 1.0), GUIDE_CONFIDENCE_MAX)
        score *= 1.0 + 0.1 * confidence
    if memory.recall_count:
        score *= 1.0 + math.log1p(memory.recall_count) * settings.memory_usage_score_weight
    return score


@dataclass
class Digest:
    id: str
    title: str
    score: float
    snippet: str
    type: str | None
    tags: list[str]
    space_id: str
    kind: str
    created_by: str | None = None
    updated_at: str | None = None
    expires_at: str | None = None
    updated_by: str | None = None


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
    type_tokens: Sequence[str] | None = None,
    tags: list[str] | None = None,
    tag_mode: str = "and",
    path_hint: str | None = None,
    repo_hint: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    include_expired: bool = False,
) -> list[Digest]:
    settings = get_settings()
    if not (query or "").strip():
        return []
    embedder = get_embedder()
    qvec = await embedder.aembed(query)
    limit = settings.resolve_search_limit(limit)
    offset = max(0, int(offset))
    # One candidate window for every page of a query. A window that grows
    # with ``offset`` changes the fused scores between page requests, and an
    # unbounded ``offset`` would fetch an unbounded number of rows.
    fetch_n = max(settings.result_max_limit * 3, 30)
    now = datetime.now(UTC)

    base_filters = []
    active = active_memory_clause(include_expired=include_expired, now=now)
    if active is not None:
        base_filters.append(active)
    if space_ids is not None:
        if not space_ids:
            return []
        base_filters.append(Memory.space_id.in_(space_ids))
    if type_tokens is not None:
        clause = memory_type_clause(type_tokens)
        if clause is not None:
            base_filters.append(clause)
    if path_hint:
        # Match the path tag or a mention in the body, as ``about_path``
        # does. ``rerank_score`` boosts the rows whose tag matches.
        path_pat = f"%{escape_ilike(path_hint)}%"
        base_filters.append(
            or_(
                Memory.path_hint.ilike(path_pat, escape="\\"),
                Memory.body.ilike(path_pat, escape="\\"),
            )
        )
    if repo_hint:
        base_filters.append(Memory.repo_hint.ilike(f"%{escape_ilike(repo_hint)}%", escape="\\"))
    if tags:
        if tag_mode == "or":
            base_filters.append(Memory.tags.overlap(tags))
        else:
            base_filters.append(Memory.tags.contains(tags))

    # Vector branch. pgvector cosine_distance = 1 - cosine_similarity, and the
    # embeddings are unit vectors, so "similarity >= floor" is
    # "distance <= 1 - floor". Keyword matches are not subject to this floor.
    distance = Memory.embedding.cosine_distance(qvec)
    vec_stmt = (
        select(Memory.id)
        .where(Memory.embedding.is_not(None), *base_filters)
        .where(distance <= 1.0 - settings.search_min_similarity)
        .order_by(distance)
        .limit(fetch_n)
    )
    vec_ids = [row[0] for row in (await session.execute(vec_stmt)).all()]

    # Keyword branch: three steps, the first that returns rows wins. An
    # empty query has no keyword leg, as before.
    fts_ids: list[uuid.UUID] = []
    stripped = query.strip()
    if stripped:
        # 1. Every term. websearch_to_tsquery joins unquoted terms with AND.
        all_terms = func.websearch_to_tsquery("english", query)
        fts_stmt = (
            select(Memory.id)
            .where(*base_filters)
            .where(Memory.search_tsv.op("@@")(all_terms))
            .order_by(func.ts_rank_cd(Memory.search_tsv, all_terms).desc(), Memory.id)
            .limit(fetch_n)
        )
        fts_ids = [row[0] for row in (await session.execute(fts_stmt)).all()]
        # 2. The whole query as a substring of title or body. This is a
        #    sequential scan, so it runs for short queries only.
        if not fts_ids and len(stripped) <= SUBSTRING_FALLBACK_MAX_CHARS:
            like = f"%{escape_ilike(stripped)}%"
            like_stmt = (
                select(Memory.id)
                .where(*base_filters)
                .where(
                    or_(
                        Memory.title.ilike(like, escape="\\"),
                        Memory.body.ilike(like, escape="\\"),
                    )
                )
                .order_by(Memory.updated_at.desc(), Memory.id)
                .limit(fetch_n)
            )
            fts_ids = [row[0] for row in (await session.execute(like_stmt)).all()]
        # 3. Any term, among the vector candidates only. They passed the
        #    filters and the similarity floor already, so a shared common
        #    word cannot bring in an unrelated memory, and the statement
        #    reads at most ``fetch_n`` rows by primary key.
        or_text = any_term_query(query)
        if not fts_ids and vec_ids and or_text:
            any_term = func.websearch_to_tsquery("english", or_text)
            any_stmt = (
                select(Memory.id)
                .where(Memory.id.in_(vec_ids))
                .where(Memory.search_tsv.op("@@")(any_term))
                .order_by(func.ts_rank_cd(Memory.search_tsv, any_term).desc(), Memory.id)
                .limit(fetch_n)
            )
            fts_ids = [row[0] for row in (await session.execute(any_stmt)).all()]

    fused = rrf_fuse([vec_ids, fts_ids], k=settings.rrf_k)
    if not fused:
        return []

    rrf_map = dict(fused)
    rows = (
        (
            await session.execute(
                select(Memory).options(defer(Memory.embedding)).where(Memory.id.in_(list(rrf_map)))
            )
        )
        .scalars()
        .all()
    )
    # Sort by score, then by id, so equal scores have one fixed order and
    # every page is a slice of the same list.
    scored = sorted(
        ((m, rerank_score(m, rrf_map[m.id], now=now, path_hint=path_hint)) for m in rows),
        key=lambda pair: (-pair[1], str(pair[0].id)),
    )[offset : offset + limit]
    digests: list[Digest] = []
    for m, score in scored:
        digests.append(
            Digest(
                id=str(m.id),
                title=m.title,
                score=round(score, 6),
                snippet=_snippet(m.body),
                type=m.entry_type.value if m.entry_type else None,
                tags=list(m.tags or []),
                space_id=str(m.space_id),
                kind=m.kind.value,
                created_by=str(m.created_by_user_id) if m.created_by_user_id else None,
                updated_by=str(m.updated_by_user_id) if m.updated_by_user_id else None,
                updated_at=m.updated_at.isoformat() if m.updated_at else None,
                expires_at=m.expires_at.isoformat() if m.expires_at else None,
            )
        )
    return digests
