"""Keyword leg: a long query still gives credit to memories that share terms."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

import memchentic.search.hybrid as hybrid_module
from memchentic.search.hybrid import any_term_query
from memchentic.services import guides as guide_service
from memchentic.services import memories as memory_service
from memchentic.services import memory_hygiene
from support import call_tool, register_user


class _OneAxisEmbedder:
    """Every text maps to the same unit vector: the vector leg cannot decide."""

    backend = "fake"

    @staticmethod
    def _vec() -> list[float]:
        vec = [0.0] * 384
        vec[0] = 1.0
        return vec

    def embed_memory_text(self, _title: str, _body: str) -> list[float]:
        return self._vec()

    async def aembed_memory_text(self, _title: str, _body: str) -> list[float]:
        return self._vec()

    async def aembed(self, _text: str) -> list[float]:
        return self._vec()


@pytest.fixture
def one_axis_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _OneAxisEmbedder()
    monkeypatch.setattr(hybrid_module, "get_embedder", lambda: fake)
    monkeypatch.setattr(memory_service, "get_embedder", lambda: fake)
    monkeypatch.setattr(memory_hygiene, "get_embedder", lambda: fake)
    monkeypatch.setattr(guide_service, "get_embedder", lambda: fake)


def _user(client: TestClient) -> dict[str, str]:
    return register_user(client).headers


def _tool(client: TestClient, auth: dict[str, str], name: str, args: dict[str, Any]) -> Any:
    return call_tool(client, auth, name, args)["structuredContent"]


def _create(client: TestClient, auth: dict[str, str], title: str, body: str) -> dict[str, Any]:
    resp = client.post(
        "/api/memories",
        headers=auth,
        json={"title": title, "body": body, "type": "fact", "force": True},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_any_term_query_joins_distinct_words_with_or() -> None:
    assert (
        any_term_query('How do I "rotate" the -ingress cert OR key, the cert?')
        == "how or do or i or rotate or the or ingress or cert or key"
    )
    assert any_term_query("  ?!  ") == ""


def test_long_query_gives_keyword_credit_to_partial_matches(
    client: TestClient, one_axis_embedder: None
) -> None:
    auth = _user(client)
    target = _create(
        client,
        auth,
        "Ingress certificate rotation",
        "Rotate the TLS certificate of the ingress controller with cert-manager",
    )
    decoy = _create(client, auth, "Sourdough hydration", "seventy five percent with a cold proof")
    # 71 characters; "expires" is in no memory, so no memory has every term.
    query = "how do we rotate the ingress tls certificate before the old one expires"

    hits = _tool(client, auth, "find", {"query": query})["result"]

    by_id = {h["id"]: h for h in hits}
    assert set(by_id) == {target["id"], decoy["id"]}
    assert hits[0]["id"] == target["id"]
    # RRF with k = 60; both rows are seconds old. The recency factor is at
    # most 1.0 until BUG-001 is applied and at most 1.25 after it.
    # Target, two legs: at least 1/62 + 1/61 = 0.0325 (0.0406 after BUG-001).
    # Decoy, one leg: at most 1/61 = 0.0164 (0.0205 after BUG-001).
    assert by_id[target["id"]]["score"] >= 0.03
    assert by_id[decoy["id"]]["score"] <= 0.021
