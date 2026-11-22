"""Postgres-backed sliding-window rate limiter for auth surfaces.

Every application replica shares the same counters via
``auth_rate_limit_hits``, so raising ``replicaCount`` does not multiply the
effective limit.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote, urlparse

import asyncpg
from fastapi import HTTPException, Request
from sqlalchemy import text

from memchentic.db.session import advisory_xact_lock, get_session_factory
from memchentic.settings import get_settings

# Drop hits older than this even when a caller uses a shorter window, so the
# table stays bounded under rotating bucket keys.
_RETENTION = timedelta(hours=2)


async def check_rate_limit(bucket: str, *, limit: int, window_seconds: int) -> None:
    """Raise 429 when ``bucket`` has exceeded ``limit`` hits in ``window_seconds``.

    Args:
        bucket: Isolation key (e.g. ``login-ip:{ip}``). Keep it short: hash
            any caller-supplied text before putting it in the key.
        limit: Maximum allowed hits inside the window.
        window_seconds: Sliding window length in seconds.

    Raises:
        HTTPException: 429 when the limit is exceeded.
    """
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=window_seconds)
    retention_cutoff = now - _RETENTION
    async with get_session_factory()() as session:
        await advisory_xact_lock(session, f"memchentic:ratelimit:{bucket}")
        await session.execute(
            text("DELETE FROM auth_rate_limit_hits WHERE bucket = :bucket AND hit_at < :cut"),
            {"bucket": bucket, "cut": retention_cutoff},
        )
        count = await session.scalar(
            text(
                "SELECT count(*) FROM auth_rate_limit_hits "
                "WHERE bucket = :bucket AND hit_at >= :cutoff"
            ),
            {"bucket": bucket, "cutoff": cutoff},
        )
        if int(count or 0) >= limit:
            await session.commit()
            raise HTTPException(429, "Too many requests")
        await session.execute(
            text(
                "INSERT INTO auth_rate_limit_hits (bucket, hit_at) "
                "VALUES (:bucket, :hit_at)"
            ),
            {"bucket": bucket, "hit_at": now},
        )
        await session.commit()


def reset_rate_limits() -> None:
    """Clear all rate-limit hits (tests only).

    Uses a one-shot asyncpg connection in a worker thread so it never binds
    the shared SQLAlchemy engine to a secondary event loop.
    """

    async def _clear() -> None:
        settings = get_settings()
        parsed = urlparse(
            settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        )
        ssl_arg: object = False
        if settings.postgres_sslmode != "disable":
            ssl_arg = settings.postgres_ssl
        conn = await asyncpg.connect(
            host=parsed.hostname or "localhost",
            port=parsed.port or 5432,
            user=unquote(parsed.username or ""),
            password=unquote(parsed.password or ""),
            database=(parsed.path or "/").lstrip("/") or "memchentic",
            ssl=ssl_arg,
        )
        try:
            await conn.execute("DELETE FROM auth_rate_limit_hits")
        except asyncpg.UndefinedTableError:
            pass
        finally:
            await conn.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(lambda: asyncio.run(_clear())).result(timeout=30)


def client_ip(request: Request) -> str:
    """Client address for per-client rate-limit buckets.

    ``request.client`` is the TCP peer unless uvicorn trusts that peer to set
    ``X-Forwarded-For`` (environment variable ``FORWARDED_ALLOW_IPS``). Never
    read ``X-Forwarded-For`` here: a client can send any value in it.

    Args:
        request: Incoming request.

    Returns:
        The client host, or ``"unknown"`` when the server did not provide one.
    """
    return request.client.host if request.client else "unknown"
