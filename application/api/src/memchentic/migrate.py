"""Create extensions and apply Alembic migrations as the owner role, then exit.

Run as a one-shot process when the application starts with
``RUN_MIGRATIONS_ON_BOOT=false``::

    python -m memchentic.migrate
"""

from __future__ import annotations

import asyncio
import logging

from memchentic.db.session import init_db
from memchentic.settings import get_settings

logger = logging.getLogger("memchentic.migrate")


async def migrate() -> None:
    """Create extensions and upgrade the schema to head.

    Raises:
        RuntimeError: A required secret is empty, a placeholder, or too short.
    """
    # Imported here: memchentic.main builds the FastAPI app when it is imported.
    from memchentic.main import _run_migrations

    settings = get_settings()
    problems = settings.insecure_secret_problems(for_migration=True)
    if problems:
        details = "; ".join(f"{name} {reason}" for name, reason in problems.items())
        raise RuntimeError(f"Refusing to migrate: {details}")
    await init_db()
    await asyncio.to_thread(_run_migrations)
    logger.info("Migrations applied")


def main() -> None:
    """Entry point for ``python -m memchentic.migrate``."""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(migrate())


if __name__ == "__main__":
    main()
