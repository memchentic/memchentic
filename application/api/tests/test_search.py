"""Unit tests for hybrid rank fusion and the embedder contract."""

from __future__ import annotations

import math
import uuid

from memchentic.embeddings.loader import EMBEDDING_DIM, Embedder
from memchentic.search.hybrid import rrf_fuse


def test_rrf_rewards_items_ranked_by_both_branches() -> None:
    """An id present in both the vector and keyword lists outranks single-branch ids."""
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fused = rrf_fuse([[a, b], [c, b]], k=60)
    assert fused[0][0] == b
    assert {item for item, _ in fused} == {a, b, c}


def test_rrf_handles_empty_branch() -> None:
    """Fusion degrades to the non-empty branch order when the other branch is empty."""
    a, b = uuid.uuid4(), uuid.uuid4()
    assert [item for item, _ in rrf_fuse([[a, b], []], k=60)] == [a, b]


def test_hash_fallback_is_deterministic_and_normalized(tmp_path) -> None:
    """Without model files the embedder returns stable unit vectors of the configured size.

    Args:
        tmp_path: Empty directory standing in for a missing model path.
    """
    embedder = Embedder(str(tmp_path))
    assert embedder.backend == "hash"
    first = embedder.embed("postgres pgvector")
    assert first == embedder.embed("postgres pgvector")
    assert len(first) == EMBEDDING_DIM
    assert math.isclose(math.sqrt(sum(v * v for v in first)), 1.0, rel_tol=1e-6)
