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
ListenerState = Literal["listening", "reconnecting", "stopped"]

_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
_listener_conn: asyncpg.Connection | None = None
_listener_task: asyncio.Task[None] | None = None
_listener_state: ListenerState = "stopped"
_lock = asyncio.Lock()
# Identity sentinel pushed to every subscriber queue when the process starts
# shutting down. SSE generators end their stream when they receive it.
STREAM_CLOSED: dict[str, Any] = {"action": "__stream_closed__"}
_streams_closing = False

_CONNECT_TIMEOUT_SECONDS = 10.0
_PING_INTERVAL_SECONDS = 30.0
_PING_TIMEOUT_SECONDS = 10.0
_BACKOFF_INITIAL_SECONDS = 1.0
_BACKOFF_MAX_SECONDS = 30.0
_CLOSE_TIMEOUT_SECONDS = 5.0
# Failures of a lost or unreachable database; anything else is a bug and ends
# the supervisor, which health then reports as ``stopped``.
_CONNECTION_ERRORS: tuple[type[BaseException], ...] = (
    OSError,
    TimeoutError,
    asyncpg.PostgresError,
    asyncpg.InterfaceError,
)


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
        "database": unquote((parsed.path or "/").lstrip("/")) or "memchentic",
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


def close_memory_streams() -> None:
    """Ask every open SSE stream to end (called when shutdown starts).

    Sets a flag so streams that subscribe afterwards end at once, and pushes
    ``STREAM_CLOSED`` to every current subscriber. Must run on the event loop
    that owns the subscriber queues.
    """
    global _streams_closing
    _streams_closing = True
    _fan_out(STREAM_CLOSED)


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


def memory_listener_state() -> ListenerState:
    """Report the state of the LISTEN connection (used by ``/api/health``).

    Returns:
        ``listening`` while the connection is open, ``reconnecting`` while
        the supervisor is re-establishing it, and ``stopped`` before
        startup, after shutdown, or when the supervisor task has ended.
    """
    task = _listener_task
    if task is None or task.done():
        return "stopped"
    return _listener_state


async def _open_listener() -> asyncpg.Connection:
    """Open a connection, LISTEN on the channel, and mark the bus as listening.

    Returns:
        The open listener connection.
    """
    global _listener_conn, _listener_state
    conn = await asyncpg.connect(
        **_connect_kwargs_from_settings(),
        ssl=get_settings().postgres_ssl,
        timeout=_CONNECT_TIMEOUT_SECONDS,
    )
    try:
        await conn.add_listener(MEMORY_CHANNEL, _on_notify)
    except BaseException:
        conn.terminate()
        raise
    _listener_conn = conn
    _listener_state = "listening"
    logger.info("Listening on Postgres channel %s", MEMORY_CHANNEL)
    return conn


async def _wait_until_lost(conn: asyncpg.Connection) -> None:
    """Return when ``conn`` is closed or no longer answers.

    A termination listener reports a closed socket at once. A ``SELECT 1``
    every 30 seconds finds a connection that died without a close (network
    drop, proxy idle timeout).

    Args:
        conn: The listener connection to watch.
    """
    lost = asyncio.Event()

    def _on_terminate(_conn: asyncpg.Connection) -> None:
        lost.set()

    conn.add_termination_listener(_on_terminate)
    try:
        while not conn.is_closed():
            try:
                await asyncio.wait_for(lost.wait(), timeout=_PING_INTERVAL_SECONDS)
                return
            except TimeoutError:
                pass
            try:
                await conn.execute("SELECT 1", timeout=_PING_TIMEOUT_SECONDS)
            except _CONNECTION_ERRORS as exc:
                logger.warning("LISTEN connection ping failed (%s)", type(exc).__name__)
                return
    finally:
        conn.remove_termination_listener(_on_terminate)


async def _supervise_listener(conn: asyncpg.Connection) -> None:
    """Keep one LISTEN connection open until the task is cancelled.

    Args:
        conn: The connection opened by ``start_memory_listener``.
    """
    global _listener_conn, _listener_state
    while True:
        await _wait_until_lost(conn)
        _listener_state = "reconnecting"
        _listener_conn = None
        conn.terminate()
        logger.warning("Lost the LISTEN connection on %s; reconnecting", MEMORY_CHANNEL)
        delay = _BACKOFF_INITIAL_SECONDS
        while True:
            try:
                conn = await _open_listener()
                break
            except _CONNECTION_ERRORS as exc:
                logger.warning(
                    "LISTEN reconnect failed (%s: %s); next attempt in %.0f s",
                    type(exc).__name__,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, _BACKOFF_MAX_SECONDS)


async def start_memory_listener() -> None:
    """Open the LISTEN connection and start its supervisor (idempotent).

    The first connection is made here, so a database that cannot be reached
    at startup still fails startup. Later losses are repaired by the
    supervisor task.
    """
    global _listener_task, _streams_closing
    async with _lock:
        _streams_closing = False
        if _listener_task is not None and not _listener_task.done():
            return
        conn = await _open_listener()
        _listener_task = asyncio.create_task(
            _supervise_listener(conn), name="memchentic-memory-listener"
        )


async def stop_memory_listener() -> None:
    """Stop the supervisor, close the LISTEN connection, drop local subscribers."""
    global _listener_conn, _listener_task, _listener_state
    async with _lock:
        task = _listener_task
        _listener_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug("listener supervisor ended with an error", exc_info=True)
        conn = _listener_conn
        _listener_conn = None
        _listener_state = "stopped"
        close_memory_streams()
        _subscribers.clear()
        if conn is not None and not conn.is_closed():
            try:
                await conn.remove_listener(MEMORY_CHANNEL, _on_notify)
                await conn.close(timeout=_CLOSE_TIMEOUT_SECONDS)
            except Exception:
                logger.debug("closing the LISTEN connection failed", exc_info=True)
                conn.terminate()


# Postgres rejects NOTIFY payloads of 8000 bytes or more.
_NOTIFY_MAX_BYTES = 7900
# Title length kept when everything else has been dropped. JSON escaping costs
# at most 12 bytes per character, so 200 characters stay far below the limit.
_NOTIFY_TITLE_CHARS = 200


def _encode_event(event: MemoryEvent) -> str:
    """Serialize ``event`` to JSON that always fits in one NOTIFY payload.

    Optional fields are reduced in order until the payload fits: the snippet,
    then the tag list, then the tail of the title. The stored memory is not
    changed; clients read the full row on their next list or detail fetch.

    Args:
        event: Mutation payload.

    Returns:
        Compact JSON of at most ``_NOTIFY_MAX_BYTES`` bytes.
    """
    data = event.to_dict()
    payload = json.dumps(data, separators=(",", ":"))
    reductions: tuple[tuple[str, Any], ...] = (
        ("snippet", ""),
        ("tags", []),
        ("title", event.title[:_NOTIFY_TITLE_CHARS]),
    )
    for field, reduced in reductions:
        if len(payload.encode()) <= _NOTIFY_MAX_BYTES:
            break
        data[field] = reduced
        payload = json.dumps(data, separators=(",", ":"))
    return payload


async def publish_memory_event(session: AsyncSession, event: MemoryEvent) -> None:
    """Queue a NOTIFY in the caller's transaction (delivered at commit).

    Args:
        session: Session used for the mutation (same connection; NOTIFY is
            visible to listeners after commit).
        event: Compact mutation payload. Reduced by ``_encode_event`` when it
            would exceed the NOTIFY size limit.
    """
    await session.execute(
        text("SELECT pg_notify(:channel, :payload)"),
        {"channel": MEMORY_CHANNEL, "payload": _encode_event(event)},
    )


@asynccontextmanager
async def subscribe_memory_events(
    *,
    maxsize: int = 64,
) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
    """Register a local queue for memory events until the context exits.

    Yields:
        An asyncio queue that receives decoded event dicts, and the
        ``STREAM_CLOSED`` sentinel when the process is shutting down.
    """
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=maxsize)
    if _streams_closing:
        queue.put_nowait(STREAM_CLOSED)
    _subscribers.add(queue)
    try:
        yield queue
    finally:
        _subscribers.discard(queue)
