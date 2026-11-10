"""REST API router: mounts every resource router under ``/api``."""

from __future__ import annotations

from fastapi import APIRouter

from memchentic.api.routes import (
    api_keys,
    events,
    health,
    memories,
    session,
    spaces,
    system,
    users,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(events.router)
api_router.include_router(session.router)
api_router.include_router(users.router)
api_router.include_router(spaces.router)
api_router.include_router(memories.router)
api_router.include_router(api_keys.router)
api_router.include_router(system.router)
