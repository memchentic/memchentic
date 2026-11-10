"""Postgres LISTEN/NOTIFY bus for memory mutations.

One dedicated asyncpg connection listens on ``memchentic_memory``. Writers
``NOTIFY`` after commit so every application replica (Compose, Swarm, Helm)
receives the same event. Local SSE subscribers are asyncio queues fan-out
from that listener.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict, dataclass
from typing import Any, Literal
from urllib.parse import unquote, urlparse

import asyncpg
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.models.entities import Memory
from memchentic.settings import get_settings

logger = logging.getLogger("memchentic.events")

MEMORY_CHANNEL = "memchentic_memory"
Action = Literal["created", "updated", "deleted"]

_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
_listener_conn: asyncpg.Connection | None = None
_lock = asyncio.Lock()


@dataclass(frozen=True, slots=True)
class MemoryEvent:
    """Compact memory mutation payload for SSE clients.

    Attributes:
        action: Mutation kind.
        id: Memory UUID string.
        space_id: Owning space UUID string.
        kind: ``entry`` or ``guide``.
        type: Entry type, or None for guides.
        title: Memory title.
        tags: Tag list.
        snippet: Short body preview (empty on delete).
        confidence: Guide confidence when present.
        created_by: Creator user UUID string when known.
        created_at: ISO timestamp when known.
        updated_at: ISO timestamp when known.
    """

    action: Action
    id: str
    space_id: str
    kind: str
    type: str | None
    title: str
    tags: list[str]
    snippet: str = ""
    confidence: float | None = None
    created_by: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON / NOTIFY payload."""
        return asdict(self)


def memory_event_from_row(action: Action, memory: Memory) -> MemoryEvent:
    """Build an event from a persisted memory row.

    Args:
        action: Mutation kind.
        memory: SQLAlchemy memory entity (may be expired after delete snapshot).

    Returns:
        A compact ``MemoryEvent``.
    """
    body = memory.body or ""
    return MemoryEvent(
        action=action,
        id=str(memory.id),
        space_id=str(memory.space_id),
        kind=memory.kind.value,
        type=memory.entry_type.value if memory.entry_type else None,
        title=memory.title,
        tags=list(memory.tags or []),
        snippet=body[:180] + ("…" if len(body) > 180 else ""),
        confidence=float(memory.confidence) if memory.confidence is not None else None,
        created_by=str(memory.created_by_user_id) if memory.created_by_user_id else None,
        created_at=memory.created_at.isoformat() if memory.created_at else None,
        updated_at=memory.updated_at.isoformat() if memory.updated_at else None,
    )


def _connect_kwargs_from_settings() -> dict[str, Any]:
    """Parse the SQLAlchemy async URL into asyncpg connect kwargs.

    Passes user/password as discrete kwargs so reserved URL characters in the
    password (``/``, ``@``, ``:``, ``#``) do not break the listener DSN.
    """
    url = get_settings().database_url
    # postgresql+asyncpg://user:pass@host:port/db
    parsed = urlparse(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    return {
        "user": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "database": (parsed.path or "/").lstrip("/") or "memchentic",
    }


def _fan_out(payload: dict[str, Any]) -> None:
    """Push a decoded event onto every local subscriber queue.

    A full queue drops the oldest event so a slow client keeps receiving
    newer mutations instead of being removed from the bus.
    """
    for queue in list(_subscribers):
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            with suppress(asyncio.QueueEmpty):
                queue.get_nowait()
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                logger.debug("Dropping memory event; subscriber still backed up")


def _on_notify(
    _connection: asyncpg.Connection,
    _pid: int,
    channel: str,
    payload: str,
) -> None:
    """Handle one asyncpg notification (runs on the listener connection)."""
    if channel != MEMORY_CHANNEL:
        return
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        logger.warning("Ignoring malformed memory notify payload")
        return
    if not isinstance(data, dict) or "action" not in data or "id" not in data:
        logger.warning("Ignoring incomplete memory notify payload")
        return
    _fan_out(data)


async def start_memory_listener() -> None:
    """Open the dedicated LISTEN connection (idempotent)."""
    global _listener_conn
    async with _lock:
        if _listener_conn is not None:
            return
        conn = await asyncpg.connect(
            **_connect_kwargs_from_settings(),
            ssl=get_settings().postgres_ssl,
        )
        await conn.add_listener(MEMORY_CHANNEL, _on_notify)
        _listener_conn = conn
        logger.info("Listening on Postgres channel %s", MEMORY_CHANNEL)


async def stop_memory_listener() -> None:
    """Close the LISTEN connection and drop local subscribers."""
    global _listener_conn
    async with _lock:
        conn = _listener_conn
        _listener_conn = None
        _subscribers.clear()
        if conn is not None:
            try:
                await conn.remove_listener(MEMORY_CHANNEL, _on_notify)
            except Exception:
                logger.debug("remove_listener during shutdown failed", exc_info=True)
            await conn.close()


async def publish_memory_event(session: AsyncSession, event: MemoryEvent) -> None:
    """NOTIFY after the caller's transaction has committed.

    Args:
        session: Session used for the mutation (same connection; NOTIFY is
            visible to listeners after commit).
        event: Compact mutation payload.
    """
    payload = json.dumps(event.to_dict(), separators=(",", ":"))
    if len(payload.encode()) > 7900:
        # Stay under Postgres NOTIFY ~8KiB; drop snippet if needed.
        slim = event.to_dict()
        slim["snippet"] = ""
        payload = json.dumps(slim, separators=(",", ":"))
    await session.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {"channel": MEMORY_CHANNEL, "payload": payload},
    )


@asynccontextmanager
async def subscribe_memory_events(
    *,
    maxsize: int = 64,
) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
    """Register a local queue for memory events until the context exits.

    Yields:
        An asyncio queue that receives decoded event dicts.
    """
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)
    _subscribers.add(queue)
    try:
        yield queue
    finally:
        _subscribers.discard(queue)
