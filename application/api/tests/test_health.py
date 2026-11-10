"""Health endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_exposes_embedder(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "memchentic"
    assert "embedder" in body
    assert body["status"] in {"ok", "degraded"}
