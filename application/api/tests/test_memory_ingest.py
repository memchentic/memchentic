"""Ingest suggest heuristics."""

from __future__ import annotations

from fastapi.testclient import TestClient

from memchentic.services.memory_ingest import suggest_from_text
from support import register_user

SUPPORT_CHAT = """
# Support chat summary

The customer asked about billing cycles. We explained that renewals run on the
first of each month and that receipts are emailed within an hour.

## Escalation policy

When a ticket is reopened twice, route it to the on-call engineer and attach
the last transcript.
"""

OPS_RUNBOOK = """
## Database backup

Run the scheduled snapshot before schema migrations. Keep at least seven daily
copies on object storage.

## Rollback

If migration step three fails, restore the previous release image and replay
events from the outbox table.
"""


def test_heuristic_support_chat() -> None:
    rows = suggest_from_text(SUPPORT_CHAT)
    titles = {r["title"] for r in rows}
    assert "Support chat summary" in titles
    assert "Escalation policy" in titles
    assert any(r["kind"] == "guide" for r in rows)


def test_heuristic_ops_runbook() -> None:
    rows = suggest_from_text(OPS_RUNBOOK)
    titles = {r["title"] for r in rows}
    assert "Database backup" in titles
    assert "Rollback" in titles


def test_rest_ingest_suggest(client: TestClient) -> None:
    auth = register_user(client).headers
    resp = client.post(
        "/api/memories/ingest/suggest",
        headers=auth,
        json={"text": OPS_RUNBOOK, "use_llm": False},
    )
    assert resp.status_code == 200, resp.text
    candidates = resp.json()["candidates"]
    assert len(candidates) >= 2
