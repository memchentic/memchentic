"""Pagination: search pages are slices of one list."""

from __future__ import annotations

import math
import re
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

import memchentic.search.hybrid as hybrid_module
from memchentic.services import guides as guide_service
from memchentic.services import memories as memory_service
from memchentic.services import memory_hygiene
from support import call_tool, register_user


class _AngleEmbedder:
    """Deterministic stand-in for MiniLM.

    A memory whose title contains ``nNN`` maps to the unit vector at angle
    ``NN * 0.02`` radians. Every query maps to angle 0. A smaller NN is a
    closer vector, and every cosine similarity is above 0.7.
    """

    backend = "fake"

    @staticmethod
    def _at(angle: float) -> list[float]:
        vec = [0.0] * 384
        vec[0] = math.cos(angle)
        vec[1] = math.sin(angle)
        return vec

    def embed_memory_text(self, title: str, _body: str) -> list[float]:
        match = re.search(r"\bn(\d\d)\b", title)
        return self._at(int(match.group(1)) * 0.02 if match else 0.0)

    async def aembed_memory_text(self, title: str, body: str) -> list[float]:
        return self.embed_memory_text(title, body)

    async def aembed(self, _text: str) -> list[float]:
        return self._at(0.0)


@pytest.fixture
def angle_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _AngleEmbedder()
    monkeypatch.setattr(hybrid_module, "get_embedder", lambda: fake)
    monkeypatch.setattr(memory_service, "get_embedder", lambda: fake)
    monkeypatch.setattr(memory_hygiene, "get_embedder", lambda: fake)
    monkeypatch.setattr(guide_service, "get_embedder", lambda: fake)


def _user(client: TestClient) -> dict[str, str]:
    return register_user(client).headers


def _tool(
    client: TestClient, auth: dict[str, str], name: str, args: dict[str, Any]
) -> dict[str, Any]:
    """Call an MCP tool and return the JSON-RPC ``result`` (may carry ``isError``)."""
    return call_tool(client, auth, name, args)


def test_find_pages_match_the_single_call_order(client: TestClient, angle_embedder: None) -> None:
    auth = _user(client)
    marker = uuid.uuid4().hex[:10]
    word = f"w{marker}"
    for n in range(1, 36):
        # Vector leg: n01 is the nearest row. Keyword leg: n35 ranks first,
        # because its body holds the query word 35 times.
        resp = client.post(
            "/api/memories",
            headers=auth,
            json={
                "title": f"page {marker} n{n:02d}",
                "body": " ".join([word] * n),
                "type": "note",
                "force": True,
            },
        )
        assert resp.status_code == 200, resp.text

    full = [
        h["id"]
        for h in _tool(client, auth, "find", {"query": word, "limit": 100})["structuredContent"][
            "result"
        ]
    ]
    assert len(full) == 35

    paged: list[str] = []
    for offset in range(0, 35, 5):
        page = _tool(client, auth, "find", {"query": word, "limit": 5, "offset": offset})[
            "structuredContent"
        ]["result"]
        paged.extend(h["id"] for h in page)

    assert len(set(paged)) == 35
    assert paged == full

    past_the_end = _tool(client, auth, "find", {"query": word, "limit": 5, "offset": 100000})
    assert past_the_end["structuredContent"]["result"] == []
