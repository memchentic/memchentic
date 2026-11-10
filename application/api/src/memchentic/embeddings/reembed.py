"""Recompute every stored embedding with the active embedder backend.

Run after the MiniLM ONNX files are present so hash-fallback vectors are not
mixed with model vectors, and after embedder changes that alter vectors
(for example attention-mask / padding fixes)::

    python -m memchentic.embeddings.reembed
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from memchentic.db.session import get_session_factory, set_rls_context
from memchentic.embeddings import get_embedder
from memchentic.models.entities import Memory


async def reembed_all(batch_size: int = 200) -> int:
    """Re-embed all memories in batches as the admin principal.

    Args:
        batch_size: Rows loaded and committed per batch.

    Returns:
        Number of rows whose embedding was recomputed.
    """
    embedder = get_embedder()
    total = 0
    async with get_session_factory()() as session:
        await set_rls_context(session, user_id=None, is_admin=True)
        offset = 0
        while True:
            rows = (
                (
                    await session.execute(
                        select(Memory).order_by(Memory.id).offset(offset).limit(batch_size)
                    )
                )
                .scalars()
                .all()
            )
            if not rows:
                break
            for memory in rows:
                memory.embedding = embedder.embed_memory_text(memory.title, memory.body)
            await session.commit()
            total += len(rows)
            offset += batch_size
    return total


def main() -> None:
    """Entry point: re-embed everything and print the backend and row count."""
    count = asyncio.run(reembed_all())
    # Command-line output of this script.
    print(f"backend={get_embedder().backend} reembedded={count}")  # noqa: T201


if __name__ == "__main__":
    main()
