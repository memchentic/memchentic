"""Relevance floor: unrelated queries return nothing and renew nothing."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

import memchentic.search.hybrid as hybrid_module
from memchentic.services import guides as guide_service
from memchentic.services import memories as memory_service
from memchentic.services import memory_hygiene


class _AxisEmbedder:
    """Deterministic stand-in for MiniLM.

    Every memory maps to axis 1. A query maps to axis 0 (cosine similarity 0
    to every memory) unless it starts with ``related:``; then it maps to axis
    1 (cosine similarity 1 to every memory).
    """

    backend = "fake"

    @staticmethod
    def _axis(index: int) -> list[float]:
        vec = [0.0] * 384
        vec[index] = 1.0
        return vec

    def embed_memory_text(self, _title: str, _body: str) -> list[float]:
        return self._axis(1)

    async def aembed_memory_text(self, _title: str, _body: str) -> list[float]:
        return self._axis(1)

    async def aembed(self, text: str) -> list[float]:
        return self._axis(1 if text.startswith("related:") else 0)


@pytest.fixture
def axis_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _AxisEmbedder()
    monkeypatch.setattr(hybrid_module, "get_embedder", lambda: fake)
    monkeypatch.setattr(memory_service, "get_embedder", lambda: fake)
    monkeypatch.setattr(memory_hygiene, "get_embedder", lambda: fake)
    monkeypatch.setattr(guide_service, "get_embedder", lambda: fake)


def _user(client: TestClient) -> dict[str, str]:
    email = f"floor{uuid.uuid4().hex[:10]}@example.com"
    password = f"pw-{uuid.uuid4().hex}"
    client.post("/api/auth/register", json={"email": email, "password": password})
    token = client.post("/api/auth/login", json={"email": email, "password": password}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {token}"}


def _tool(client: TestClient, auth: dict[str, str], name: str, args: dict[str, Any]) -> Any:
    resp = client.post(
        "/mcp",
        headers={**auth, "Accept": "application/json, text/event-stream"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        },
    )
    resp.raise_for_status()
    return resp.json()["result"]["structuredContent"]


def _create(client: TestClient, auth: dict[str, str], title: str, body: str) -> dict[str, Any]:
    resp = client.post(
        "/api/memories",
        headers=auth,
        json={"title": title, "body": body, "type": "fact", "ttl_days": 2, "force": True},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _listed(client: TestClient, auth: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Read rows through browse, which does not count as a recall."""
    rows = client.get("/api/memories", headers=auth, params={"limit": 100}).json()
    return {row["id"]: row for row in rows}


def test_unrelated_query_returns_nothing_and_renews_nothing(
    client: TestClient, axis_embedder: None
) -> None:
    auth = _user(client)
    created = [
        _create(client, auth, "Postgres vacuum tuning", "autovacuum scale factor for big tables"),
        _create(client, auth, "Sourdough hydration", "seventy five percent with a cold proof"),
        _create(client, auth, "Bicycle tire pressure", "run 28mm tires at 70 psi"),
    ]
    query = "kubernetes ingress tls certificate rotation"

    assert _tool(client, auth, "find", {"query": query})["result"] == []
    rest = client.get("/api/memories/search", headers=auth, params={"q": query})
    assert rest.status_code == 200, rest.text
    assert rest.json() == []

    rows = _listed(client, auth)
    for memory in created:
        row = rows[memory["id"]]
        assert row["recall_count"] == 0
        assert row["last_recalled_at"] is None
        assert row["expires_at"] == memory["expires_at"]


def test_keyword_match_is_kept_below_the_floor(client: TestClient, axis_embedder: None) -> None:
    auth = _user(client)
    marker = uuid.uuid4().hex[:12]
    wanted = _create(client, auth, f"runbook {marker}", "promote the standby")
    _create(client, auth, "Sourdough hydration", "seventy five percent with a cold proof")

    # Full-text match: the query is the marker word. Cosine similarity is 0.
    hits = _tool(client, auth, "find", {"query": marker})["result"]
    assert [h["id"] for h in hits] == [wanted["id"]]

    # Substring match (ILIKE fallback): part of the marker is not a full-text term.
    hits = _tool(client, auth, "find", {"query": marker[1:9]})["result"]
    assert [h["id"] for h in hits] == [wanted["id"]]


def test_semantic_match_without_keywords_is_kept(client: TestClient, axis_embedder: None) -> None:
    auth = _user(client)
    wanted = _create(client, auth, "Standby promotion", "steps to fail over the database")

    hits = _tool(client, auth, "find", {"query": "related: zzqx unmatched words"})["result"]
    assert [h["id"] for h in hits] == [wanted["id"]]
    assert _listed(client, auth)[wanted["id"]]["recall_count"] == 1
