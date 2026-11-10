"""Shared helpers for the test suite.

pytest inserts this directory into ``sys.path``, so ``conftest.py`` and the
test modules import this file as ``support``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import queue
import secrets
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from memchentic.settings import get_settings


@dataclass(frozen=True)
class RegisteredUser:
    """A freshly registered internal user with a valid access token."""

    token: str
    user_id: str
    email: str
    password: str

    @property
    def headers(self) -> dict[str, str]:
        """Authorization header for REST and MCP calls."""
        return {"Authorization": f"Bearer {self.token}"}


def register_user(client: TestClient, *, prefix: str = "t") -> RegisteredUser:
    """Register and log in a new internal user.

    Both steps are asserted, so a disabled registration fails here with a
    clear message instead of as a ``KeyError`` in the calling test.
    """
    email = f"{prefix}{uuid.uuid4().hex[:10]}@example.com"
    password = f"pw-{uuid.uuid4().hex}"
    reg = client.post("/api/auth/register", json={"email": email, "password": password})
    assert reg.status_code == 200, f"register failed: {reg.status_code} {reg.text}"
    login = client.post("/api/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, f"login failed: {login.status_code} {login.text}"
    return RegisteredUser(
        token=login.json()["access_token"],
        user_id=reg.json()["id"],
        email=email,
        password=password,
    )


def call_tool(
    client: TestClient, headers: dict[str, str], name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Call an MCP tool over ``POST /mcp`` and return the JSON-RPC ``result`` object."""
    resp = client.post(
        "/mcp",
        headers={**headers, "Accept": "application/json, text/event-stream"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    resp.raise_for_status()
    return resp.json()["result"]


def tool_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the rows of a list-returning tool.

    FastMCP wraps a list return value as ``{"result": [...]}`` in
    ``structuredContent``.
    """
    assert result.get("isError") is not True, result
    content = result["structuredContent"]
    return content if isinstance(content, list) else content["result"]


def pkce_pair() -> tuple[str, str]:
    """Return a PKCE ``(verifier, S256 challenge)`` pair."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def create_group(client: TestClient, headers: dict[str, str], *, prefix: str = "g") -> str:
    """Create a group as the caller and return its space id."""
    resp = client.post(
        "/api/spaces", headers=headers, json={"name": f"{prefix}-{uuid.uuid4().hex[:8]}"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def add_group_member(
    client: TestClient, owner_headers: dict[str, str], group_id: str, member: RegisteredUser
) -> None:
    """Make ``member`` an accepted member of ``group_id``.

    This is the single place that knows how membership is granted: the owner
    invites the user and the user accepts (the consent flow of SEC-001).
    """
    invited = client.post(
        f"/api/spaces/{group_id}/members",
        headers=owner_headers,
        json={"user_id": member.user_id},
    )
    assert invited.status_code == 200, invited.text
    accepted = client.post(f"/api/spaces/{group_id}/accept", headers=member.headers)
    assert accepted.status_code == 200, accepted.text


def run_owner_statements(
    statements: list[tuple[str, dict[str, Any]]],
) -> list[list[tuple[Any, ...]]]:
    """Run SQL as the owner role on a private connection and event loop.

    A worker thread owns the event loop, so this is safe to call from sync
    fixtures, from sync tests and from inside async tests.
    """

    async def _run() -> list[list[tuple[Any, ...]]]:
        engine = create_async_engine(get_settings().migration_database_url, echo=False)
        try:
            out: list[list[tuple[Any, ...]]] = []
            async with engine.begin() as conn:
                for sql, params in statements:
                    result = await conn.execute(text(sql), params)
                    rows = result.fetchall() if result.returns_rows else []
                    out.append([tuple(row) for row in rows])
            return out
        finally:
            await engine.dispose()

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, _run()).result()


def run_owner_sql(sql: str, params: dict[str, Any] | None = None) -> list[tuple[Any, ...]]:
    """Run one statement as the owner role and return its rows (empty for DML)."""
    return run_owner_statements([(sql, params or {})])[0]


def restore_shared_rows() -> None:
    """Put the instance-wide rows that tests change back to their boot values."""
    register = "true" if get_settings().register_enabled else "false"
    run_owner_statements(
        [
            (
                "UPDATE instance_settings SET value = :value "
                "WHERE key = 'register_enabled' AND value <> :value",
                {"value": register},
            ),
            (
                "UPDATE auth_groups SET idp_group_id = NULL, idp_group_name = NULL "
                "WHERE idp_group_id IS NOT NULL",
                {},
            ),
        ]
    )


class MemoryEventsTap:
    """Thread-safe subscriber for the in-process memory event bus.

    The bus touches a subscriber only in ``_fan_out``: it calls
    ``put_nowait(payload)``, and ``get_nowait()`` only after ``put_nowait``
    raised ``asyncio.QueueFull``. ``put_nowait`` here never raises, so the
    class needs no other queue method. The bus runs in the application's
    event loop thread and the test reads from the test thread, so the
    buffer is a ``queue.Queue`` and ``wait_for`` blocks instead of polling.
    """

    def __init__(self) -> None:
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()

    def put_nowait(self, payload: dict[str, Any]) -> None:
        """Receive one event from the bus."""
        self._events.put(payload)

    def wait_for(self, memory_id: str, *, timeout: float = 5.0) -> dict[str, Any] | None:
        """Block until an event for ``memory_id`` arrives; ``None`` on timeout."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                event = self._events.get(timeout=remaining)
            except queue.Empty:
                return None
            if event.get("id") == memory_id:
                return event
