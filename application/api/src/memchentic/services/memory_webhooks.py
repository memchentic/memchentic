"""Outbound webhooks for memory mutation events (post-commit)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
from typing import Any

import httpx

from memchentic.db.session import get_session_factory, set_rls_context
from memchentic.events.memory_bus import MemoryEvent
from memchentic.services import instance_settings as instance_settings_service
from memchentic.services.users import ensure_admin_user
from memchentic.settings import get_settings

_log = logging.getLogger(__name__)


def _sign_body(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def _deliver_once(payload: dict[str, Any], url: str, secret: str) -> bool:
    body = json.dumps(payload, separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json"}
    if secret.strip():
        headers["X-Memchentic-Signature"] = _sign_body(secret.strip(), body)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, content=body, headers=headers)
            resp.raise_for_status()
    except (httpx.HTTPError, OSError) as exc:
        _log.warning("memory webhook delivery failed: %s", exc)
        return False
    return True


async def _run_delivery(payload: dict[str, Any]) -> None:
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as session:
        admin = await ensure_admin_user(session, settings)
        await set_rls_context(session, user_id=admin.id, is_admin=True)
        cfg = await instance_settings_service.get_webhook_settings(session)
        if not cfg.enabled or not cfg.url.strip():
            return
        ok = await _deliver_once(payload, cfg.url.strip(), cfg.secret)
        if not ok:
            await instance_settings_service.increment_webhook_fail_count(session)


def schedule_webhook_delivery(event: MemoryEvent) -> None:
    """Fire-and-forget POST after a successful memory mutation commit."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if loop.is_closed():
        return
    payload = {"event": "memory", **event.to_dict()}

    async def _safe() -> None:
        try:
            await _run_delivery(payload)
        except Exception:  # noqa: BLE001 — background task must not raise
            _log.exception("memory webhook task failed")

    task = loop.create_task(_safe(), name="memchentic-webhook")

    def _swallow(task_ref: asyncio.Task[None]) -> None:
        if task_ref.cancelled():
            return
        exc = task_ref.exception()
        if exc is not None:
            _log.debug("memory webhook task ended with %s", exc)

    task.add_done_callback(_swallow)
