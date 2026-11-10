"""Readiness route: GET /api/ready reflects database reachability."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import memchentic.api.router as router_module


class _UnreachableSession:
    async def __aenter__(self) -> None:
        raise ConnectionRefusedError("database is down")

    async def __aexit__(self, *exc: object) -> bool:
        return False


def test_ready_returns_200_when_database_answers(client: TestClient) -> None:
    resp = client.get("/api/ready")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ready"
    assert body["database"] == "ok"


def test_ready_returns_503_when_database_is_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(router_module, "get_session_factory", lambda: _UnreachableSession)
    resp = client.get("/api/ready")
    assert resp.status_code == 503, resp.text
    assert resp.json()["database"] == "unavailable"
    # Liveness must stay independent of the database.
    assert client.get("/api/health").status_code == 200
