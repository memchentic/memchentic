"""Usefulness feedback and search rerank signal."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from memchentic.models.entities import EntryType, Memory, MemoryKind
from memchentic.search.hybrid import rerank_score
from support import call_tool, register_user


def _auth(client: TestClient) -> dict[str, str]:
    return register_user(client).headers


def test_submit_useful_via_rest(client: TestClient) -> None:
    auth = _auth(client)
    marker = uuid.uuid4().hex[:12]
    created = client.post(
        "/api/memories",
        headers=auth,
        json={
            "title": f"fb {marker}",
            "body": f"feedback body {marker}",
            "type": "note",
            "force": True,
        },
    )
    assert created.status_code == 200, created.text
    memory_id = created.json()["id"]

    posted = client.post(
        f"/api/memories/{memory_id}/feedback",
        headers=auth,
        json={"signal": "useful"},
    )
    assert posted.status_code == 200, posted.text
    assert posted.json()["signal"] == "useful"


def test_mcp_feedback_records_signal(client: TestClient) -> None:
    auth = _auth(client)
    marker = uuid.uuid4().hex[:12]
    row = call_tool(
        client,
        auth,
        "remember",
        {"title": f"mcp fb {marker}", "body": f"body {marker}", "force": True},
    )["structuredContent"]
    out = call_tool(client, auth, "feedback", {"id": row["id"], "signal": "stale"})
    assert out["structuredContent"]["signal"] == "stale"


def test_rerank_applies_feedback_score() -> None:
    now = datetime.now(UTC)
    base_fields = {
        "space_id": uuid.uuid4(),
        "kind": MemoryKind.entry,
        "entry_type": EntryType.note,
        "title": "t",
        "body": "b",
        "tags": [],
        "confidence": 1.0,
        "created_at": now,
        "updated_at": now,
    }
    memory = Memory(id=uuid.uuid4(), **base_fields)
    low = rerank_score(memory, 0.02, now=now, feedback_score=0.0)
    high = rerank_score(memory, 0.02, now=now, feedback_score=1.0)
    assert high > low
