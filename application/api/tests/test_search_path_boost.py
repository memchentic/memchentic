"""``find`` with ``path_hint``: body mentions are found, tagged rows rank first."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

import memchentic.search.hybrid as hybrid_module
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


def _create(client: TestClient, auth: dict[str, str], **fields: Any) -> str:
    resp = client.post(
        "/api/memories", headers=auth, json={"type": "fact", "force": True, **fields}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_find_path_hint_ranks_tagged_rows_over_body_mentions(
    client: TestClient, one_axis_embedder: None
) -> None:
    auth = _user(client)
    word = f"w{uuid.uuid4().hex[:10]}"
    # The body-only row is first in the keyword leg (the query word twice).
    mention = _create(
        client,
        auth,
        title="Billing retry notes",
        body=f"{word} {word} see services/billing/api.py for the retry loop",
    )
    tagged = _create(
        client,
        auth,
        title="Billing handler",
        body=f"{word} handler notes",
        path_hint="services/billing/api.py",
    )
    _create(client, auth, title="Unrelated", body=f"{word} {word} {word} nothing about paths")

    hits = _tool(client, auth, "find", {"query": word, "path_hint": "services/billing"})["result"]

    assert [h["id"] for h in hits] == [tagged, mention]
