"""Server-sent events stream of memory mutations."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from memchentic.auth.bearer import resolve_bearer
from memchentic.auth.principal import bearer_token_from_header
from memchentic.db.session import get_session_factory, set_rls_context
from memchentic.events import STREAM_CLOSED, subscribe_memory_events
from memchentic.services import spaces as space_service

router = APIRouter()


_SSE_REAUTH_SECONDS = 10.0


async def _reauth_memory_events(token: str | None) -> set[str] | None:
    """Re-resolve bearer auth and readable space ids on a fresh DB session.

    Args:
        token: Original Authorization bearer token (JWT or API key).

    Returns:
        The readable space ids when the token is still valid, otherwise
        ``None`` (caller should end the SSE stream). An empty set is a valid
        result: the caller is authenticated and can read no space.
    """
    factory = get_session_factory()
    async with factory() as session:
        principal = await resolve_bearer(session, token)
        if principal is None:
            return None
        await set_rls_context(session, user_id=principal.user_id, is_admin=principal.is_admin)
        return {
            str(sid) for sid in await space_service.accessible_space_ids(session, principal, None)
        }


@router.get("/events/memories")
async def memory_events(request: Request) -> StreamingResponse:
    """SSE stream of memory mutations the caller is allowed to see.

    Auth is resolved before the stream starts so the request does not hold a
    pooled DB session open for the lifetime of the SSE connection. The stream
    re-calls ``resolve_bearer`` and refreshes readable space ids at least every
    10s (and on idle keepalive). Invalid tokens end the stream. Clients should
    reconnect on drop; the GUI also keeps a 30s safety poll.
    """
    token = bearer_token_from_header(request.headers.get("Authorization"))
    initial = await _reauth_memory_events(token)
    if initial is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    allowed = initial

    async def gen() -> AsyncIterator[bytes]:
        nonlocal allowed
        last_reauth = time.monotonic()
        yield b": connected\n\n"
        async with subscribe_memory_events() as queue:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25.0)
                except TimeoutError:
                    refreshed = await _reauth_memory_events(token)
                    if refreshed is None:
                        return
                    allowed = refreshed
                    last_reauth = time.monotonic()
                    yield b": keepalive\n\n"
                    continue
                if event is STREAM_CLOSED:
                    # Shutdown has started: finish the response so uvicorn can
                    # run lifespan shutdown. Clients reconnect by themselves.
                    return
                now = time.monotonic()
                if now - last_reauth >= _SSE_REAUTH_SECONDS:
                    refreshed = await _reauth_memory_events(token)
                    if refreshed is None:
                        return
                    allowed = refreshed
                    last_reauth = now
                space_id = str(event.get("space_id") or "")
                if space_id not in allowed:
                    continue
                data = json.dumps(event, separators=(",", ":"))
                yield f"event: memory\ndata: {data}\n\n".encode()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
