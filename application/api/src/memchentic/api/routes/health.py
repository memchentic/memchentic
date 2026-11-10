"""Liveness and readiness probes."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from memchentic.auth.providers import provider_label
from memchentic.db.session import get_session_factory
from memchentic.embeddings import get_embedder
from memchentic.events import memory_listener_state
from memchentic.services.instance_settings import get_register_enabled
from memchentic.settings import get_settings

router = APIRouter()

logger = logging.getLogger("memchentic")


@router.get("/health")
async def health() -> dict[str, str]:
    """Report service health.

    Returns:
        ``status`` is ``ok`` only when the embedder is usable and the event
        listener is ``listening``; otherwise ``degraded``. ``events`` is the
        listener state: ``listening``, ``reconnecting`` or ``stopped``.
    """
    settings = get_settings()
    try:
        backend = get_embedder().backend
        embedder_ok = backend == "onnx" or settings.allow_hash_embeddings
    except RuntimeError:
        backend = "unavailable"
        embedder_ok = False
    events = memory_listener_state()
    status = "ok" if embedder_ok and events == "listening" else "degraded"
    return {
        "status": status,
        "service": "memchentic",
        "embedder": backend,
        "events": events,
    }


@router.get("/provider")
async def provider_info() -> dict[str, object]:
    """Expose non-secret auth configuration for the GUI.

    Returns:
        Active provider name, public URLs, and whether web OAuth Sign-in
        is available (external IdP with ``OAUTH_CLIENT_ID`` set).
    """
    live = get_settings()
    async with get_session_factory()() as session:
        register_enabled = await get_register_enabled(session)
    return {
        "oauth_provider": live.oauth_provider,
        "base_url": live.base_url,
        "mcp_url": live.mcp_resource_url,
        "web_oauth": live.web_oauth_enabled,
        "oauth_label": provider_label(live),
        "register_enabled": register_enabled,
    }


_READY_DB_TIMEOUT_SECONDS = 3.0


@router.get("/ready")
async def ready() -> JSONResponse:
    """Readiness probe: 200 only when Postgres answers and the embedder is usable.

    ``/api/health`` stays a liveness check that never touches the database, so a
    database outage removes the pod from the Service without restarting it.

    Returns:
        JSON with ``status`` (``ready`` or ``not_ready``), ``database`` and
        ``embedder``. HTTP 503 when not ready.
    """
    settings = get_settings()
    database = "ok"
    try:
        async with asyncio.timeout(_READY_DB_TIMEOUT_SECONDS), get_session_factory()() as session:
            await session.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError, TimeoutError) as exc:
        logger.warning("Readiness: database unavailable (%s)", type(exc).__name__)
        database = "unavailable"
    try:
        backend = get_embedder().backend
        embedder_ok = backend == "onnx" or settings.allow_hash_embeddings
    except RuntimeError:
        backend = "unavailable"
        embedder_ok = False
    is_ready = database == "ok" and embedder_ok
    return JSONResponse(
        {
            "status": "ready" if is_ready else "not_ready",
            "service": "memchentic",
            "database": database,
            "embedder": backend,
        },
        status_code=200 if is_ready else 503,
    )
