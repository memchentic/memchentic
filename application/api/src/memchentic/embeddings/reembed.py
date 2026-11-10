"""Recompute every stored embedding with the active embedder backend.

Run after the MiniLM ONNX files are present so hash-fallback vectors are not
mixed with model vectors, and after embedder changes that alter vectors
(for example attention-mask / padding fixes)::

    python -m memchentic.embeddings.reembed
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select, update

from memchentic.db.session import commit_with_rls, get_session_factory, set_rls_context
from memchentic.embeddings import get_embedder
from memchentic.models.entities import Memory


async def reembed_all(batch_size: int = 200) -> int:
    """Re-embed all memories in batches as the admin principal.

    Pages by primary key, writes with a core UPDATE that keeps
    ``updated_at``, and binds the admin RLS context again after every
    commit (a commit returns the connection to the pool, which clears it).

    Args:
        batch_size: Rows loaded and committed per batch.

    Returns:
        Number of rows whose embedding was recomputed.
    """
    embedder = get_embedder()
    total = 0
    last_id: uuid.UUID | None = None
    async with get_session_factory()() as session:
        await set_rls_context(session, user_id=None, is_admin=True)
        while True:
            stmt = select(Memory.id, Memory.title, Memory.body)
            if last_id is not None:
                stmt = stmt.where(Memory.id > last_id)
            rows = (await session.execute(stmt.order_by(Memory.id).limit(batch_size))).all()
            if not rows:
                break
            for memory_id, title, body in rows:
                await session.execute(
                    update(Memory)
                    .where(Memory.id == memory_id)
                    .values(
                        embedding=embedder.embed_memory_text(title, body),
                        # Re-embedding is maintenance, not an edit: keep the edit time.
                        updated_at=Memory.updated_at,
                    )
                    .execution_options(synchronize_session=False)
                )
            await commit_with_rls(session, user_id=None, is_admin=True)
            total += len(rows)
            last_id = rows[-1][0]
    return total


def main() -> None:
    """Entry point: re-embed everything and print the backend and row count."""
    count = asyncio.run(reembed_all())
    # Command-line output of this script.
    print(f"backend={get_embedder().backend} reembedded={count}")  # noqa: T201


if __name__ == "__main__":
    main()
