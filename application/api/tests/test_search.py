"""Unit tests for hybrid rank fusion and the embedder contract."""

from __future__ import annotations

import asyncio
import math
import uuid

import pytest

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


@pytest.mark.asyncio
async def test_aembed_matches_sync_embed(tmp_path) -> None:
    """Async offload returns the same vector as sync embed on the hash backend.

    Args:
        tmp_path: Empty directory standing in for a missing model path.
    """
    embedder = Embedder(str(tmp_path))
    text = "concurrent embed offload"
    assert await embedder.aembed(text) == embedder.embed(text)
    assert await embedder.aembed_memory_text("t", "b") == embedder.embed_memory_text(
        "t", "b"
    )


@pytest.mark.asyncio
async def test_concurrent_aembed_completes(tmp_path) -> None:
    """Two concurrent aembed calls both finish (awaitable offload contract).

    Args:
        tmp_path: Empty directory standing in for a missing model path.
    """
    embedder = Embedder(str(tmp_path))
    left, right = await asyncio.gather(
        embedder.aembed("alpha query"),
        embedder.aembed("beta query"),
    )
    assert len(left) == EMBEDDING_DIM
    assert len(right) == EMBEDDING_DIM
    assert left != right
