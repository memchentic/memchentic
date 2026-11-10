"""Unit tests for hybrid rank fusion and the embedder contract."""

from __future__ import annotations

import asyncio
import math
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.embeddings.loader import (
    DEFAULT_MODEL_DIR,
    EMBEDDING_DIM,
    MAX_SEQUENCE_LENGTH,
    Embedder,
)
from memchentic.search.hybrid import prepare_filtered_vector_scan, rrf_fuse

_ONNX_READY = (DEFAULT_MODEL_DIR / "model.onnx").is_file() and (
    DEFAULT_MODEL_DIR / "tokenizer.json"
).is_file()


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
    assert await embedder.aembed_memory_text("t", "b") == embedder.embed_memory_text("t", "b")


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


@pytest.mark.skipif(not _ONNX_READY, reason="MiniLM ONNX files not present")
def test_onnx_short_texts_are_not_collapsed_by_padding() -> None:
    """Unrelated short strings must not share a pad-token centroid.

    The shipped tokenizer.json pads to 128; counting pad ids as real tokens
    made short memories nearly identical. After no_padding + attention_mask,
    cosine similarity of unrelated shorts stays low.
    """
    embedder = Embedder(DEFAULT_MODEL_DIR)
    assert embedder.backend == "onnx"
    left = embedder.embed("cat sat on mat")
    right = embedder.embed("quantum cryptography keys")
    same = embedder.embed("cat sat on mat")
    assert len(left) == EMBEDDING_DIM
    assert math.isclose(math.sqrt(sum(v * v for v in left)), 1.0, rel_tol=1e-5)
    sim_unrelated = sum(a * b for a, b in zip(left, right, strict=True))
    sim_same = sum(a * b for a, b in zip(left, same, strict=True))
    assert sim_same > 0.99
    # Measured unrelated MiniLM pairs sit near 0; keep headroom for phrasing.
    assert sim_unrelated < 0.4


@pytest.mark.skipif(not _ONNX_READY, reason="MiniLM ONNX files not present")
def test_onnx_short_encode_skips_fixed_padding() -> None:
    """Embedder disables HF Fixed-128 padding so short ids stay unpadded.

    Raw tokenizer.json would emit length 128 with trailing pad ids; the loader
    calls no_padding() so encode length matches real tokens and the mask is
    all ones (attention_mask is then safe even if misread as length-only).
    """
    embedder = Embedder(DEFAULT_MODEL_DIR)
    assert embedder.backend == "onnx"
    assert embedder._tokenizer is not None
    assert embedder._tokenizer.padding is None
    encoded = embedder._tokenizer.encode("cat sat on mat")
    assert 0 < len(encoded.ids) < 128
    assert len(encoded.ids) == len(encoded.attention_mask)
    assert sum(encoded.attention_mask) == len(encoded.ids)
    assert all(bit == 1 for bit in encoded.attention_mask)


@pytest.mark.skipif(not _ONNX_READY, reason="MiniLM ONNX files not present")
def test_onnx_tokenizer_truncates_to_model_max() -> None:
    """Long text is truncated at MiniLM max length, not the HF 128 default."""
    embedder = Embedder(DEFAULT_MODEL_DIR)
    assert embedder.backend == "onnx"
    assert embedder._tokenizer is not None
    truncation = embedder._tokenizer.truncation
    assert truncation is not None
    assert truncation["max_length"] == MAX_SEQUENCE_LENGTH
    encoded = embedder._tokenizer.encode("word " * (MAX_SEQUENCE_LENGTH * 2))
    assert len(encoded.ids) == MAX_SEQUENCE_LENGTH
    assert sum(encoded.attention_mask) == MAX_SEQUENCE_LENGTH


def test_default_model_dir_points_at_api_models() -> None:
    """Model path resolves under .../models/<name> (host api tree or container root)."""
    assert DEFAULT_MODEL_DIR.name == "all-MiniLM-L6-v2"
    assert DEFAULT_MODEL_DIR.parent.name == "models"
    assert Path(DEFAULT_MODEL_DIR).is_absolute()
    assert DEFAULT_MODEL_DIR.as_posix().endswith("/models/all-MiniLM-L6-v2")


async def _hnsw_settings(session: AsyncSession) -> dict[str, str]:
    """Read the two pgvector HNSW settings of the current session.

    Args:
        session: Database session.

    Returns:
        Setting name to value, for the settings that exist.
    """
    # current_setting also reads a placeholder that set_config created before
    # pgvector was loaded in this backend; pg_settings lists only loaded ones.
    row = (
        await session.execute(
            text(
                "SELECT current_setting('hnsw.iterative_scan', true), "
                "current_setting('hnsw.ef_search', true)"
            )
        )
    ).one()
    names = ("hnsw.iterative_scan", "hnsw.ef_search")
    return {name: value for name, value in zip(names, row, strict=True) if value}


async def test_prepare_filtered_vector_scan_sets_transaction_local_setting(
    session: AsyncSession,
) -> None:
    """The helper widens the HNSW scan for the current transaction only."""
    await prepare_filtered_vector_scan(session)
    inside = await _hnsw_settings(session)
    assert (
        inside.get("hnsw.iterative_scan") == "strict_order"
        or inside.get("hnsw.ef_search") == "1000"
    )
    await session.rollback()
    after = await _hnsw_settings(session)
    assert after.get("hnsw.iterative_scan") != "strict_order"
    assert after.get("hnsw.ef_search") != "1000"
