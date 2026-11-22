"""Ingest suggest heuristics — multi-domain and adversarial fixtures."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from memchentic.services import memory_ingest
from memchentic.services.errors import ValidationError
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

RESEARCH = """
# Findings

The dataset skews toward weekend traffic. Peak load is Saturday evening UTC.

## Method note

Prefer stratified sampling over random draws for the next study.
"""

NOISE = """
ok

thanks!

LGTM

👋
"""

SECRET_BLOB = """
# Credentials note

Here is the key sk-abcdefghijklmnopqrstuvwxyz0123456789 and a password=hunter2
for the staging box. Do not share.
"""

MIXED = """
User: We prefer email only, never SMS for billing alerts.

Assistant: Understood. Escalation policy: when severity is SEV1, page on-call
within five minutes.

User: thanks
"""


def test_heuristic_support_chat() -> None:
    rows = suggest_from_text(SUPPORT_CHAT)
    titles = {r["title"] for r in rows}
    assert "Support chat summary" in titles
    assert "Escalation policy" in titles
    assert any(r["kind"] == "guide" for r in rows)
    esc = next(r for r in rows if r["title"] == "Escalation policy")
    assert esc["kind"] == "guide"
    assert "confidence" in esc
    assert esc["confidence"] >= 0.5


def test_heuristic_ops_runbook() -> None:
    rows = suggest_from_text(OPS_RUNBOOK)
    titles = {r["title"] for r in rows}
    assert "Database backup" in titles
    assert "Rollback" in titles
    assert any(r["kind"] == "guide" and r["title"] == "Rollback" for r in rows)
    assert any(r["kind"] == "guide" and r["title"] == "Database backup" for r in rows)


def test_heuristic_messages_transcript() -> None:
    rows = suggest_from_text(
        "ignored when messages set",
        messages=[
            {
                "role": "user",
                "content": "I prefer short answers unless detail is asked for.",
            },
            {
                "role": "assistant",
                "content": "Production publishes the GUI on port 18080.",
            },
            {"role": "user", "content": "ok"},
        ],
    )
    assert rows
    types = {r.get("entry_type") for r in rows if r["kind"] == "entry"}
    assert "preference" in types or any("prefer" in r["body"].lower() for r in rows)
    assert not any(r["body"].strip().lower() == "ok" for r in rows)


def test_heuristic_research_digest() -> None:
    rows = suggest_from_text(RESEARCH)
    titles = {r["title"] for r in rows}
    assert "Findings" in titles or "Method note" in titles
    assert any(r.get("entry_type") == "preference" for r in rows) or any(
        "prefer" in r["body"].lower() for r in rows
    )


def test_heuristic_path_hint_optional() -> None:
    rows = suggest_from_text(
        "## Bug\n\n"
        "The overlay hides models under application/api/src/memchentic/settings.py "
        "when bind mounts are active. Prefer fetching MiniLM before storing vectors."
    )
    assert rows
    with_path = [r for r in rows if r.get("path_hint")]
    assert with_path
    assert "settings.py" in with_path[0]["path_hint"]


def test_heuristic_noise_dropped() -> None:
    rows = suggest_from_text(NOISE)
    assert rows == []


def test_heuristic_secrets_dropped() -> None:
    rows = suggest_from_text(SECRET_BLOB)
    blob = json.dumps(rows)
    assert "sk-abcdefghijklmnopqrstuvwxyz0123456789" not in blob
    assert "hunter2" not in blob


def test_heuristic_bold_and_colon_headings() -> None:
    bold = suggest_from_text("**Escalation policy**\n\nRoute tier-2 tickets to on-call.")
    assert bold[0]["kind"] == "guide"
    assert bold[0]["title"] == "Escalation policy"
    colon = suggest_from_text("Rollback:\nRestore the prior image and replay the outbox.")
    assert colon[0]["title"] == "Rollback"
    # Mid-sentence Note: must not become a section title alone.
    prose = suggest_from_text(
        "Please take a Note: the canary stayed green for an hour after the cutover "
        "and we decided to keep the new image."
    )
    assert prose
    assert all(r["title"] != "Note" for r in prose)


def test_heuristic_mixed_chat_markdown() -> None:
    rows = suggest_from_text(MIXED.replace("\n", "\r\n"))
    assert rows
    assert any("prefer" in r["body"].lower() or r.get("entry_type") == "preference" for r in rows)
    assert not any(r["body"].strip().lower() in {"thanks", "ok"} for r in rows)


def test_heuristic_kv_json() -> None:
    rows = suggest_from_text('{"billing_cycle": "monthly", "receipt_email": true}')
    assert len(rows) >= 1
    assert all(r.get("source") == "kv" or r.get("entry_type") == "fact" for r in rows)


def test_heuristic_code_fence_dropped_unless_kv() -> None:
    code = suggest_from_text("```\ndef hello():\n    return 1\n```")
    assert code == []
    kv_fence = suggest_from_text("```\nBASE_URL: https://example.test\nPORT: 18080\n```")
    assert kv_fence


def test_heuristic_atomic_split_large_section() -> None:
    padding = "Filler sentence about the weather and lunch plans. " * 30
    body = (
        f"## Decisions\n\n{padding}"
        "We decided to keep the docs image separate from the app. "
        "Prefer short release notes unless detail is asked for."
    )
    rows = suggest_from_text(body)
    assert rows
    assert all(len(r["body"]) < 900 or r["kind"] == "guide" for r in rows)
    assert any(
        r.get("entry_type") in {"decision", "preference"} or "decided" in r["body"].lower()
        for r in rows
    )


def test_messages_validation() -> None:
    with pytest.raises(ValidationError):
        suggest_from_text("x", messages=[{"role": "user"}])  # type: ignore[list-item]
    with pytest.raises(ValidationError):
        suggest_from_text(
            "x",
            messages=[{"role": "user", "content": "hi"}] * 201,
        )


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
    assert "confidence" in candidates[0]


@pytest.mark.asyncio
async def test_llm_post_filters_junk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORY_LLM_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("MEMORY_LLM_API_KEY", "k")
    monkeypatch.setenv("MEMORY_LLM_MODEL", "test-model")
    from memchentic.settings import get_settings

    get_settings.cache_clear()

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                [
                                    {
                                        "kind": "bogus",
                                        "title": "Keep",
                                        "body": "I prefer email for invoices always.",
                                        "entry_type": "nope",
                                    },
                                    {
                                        "kind": "entry",
                                        "title": "Secret",
                                        "body": "token=sk-abcdefghijklmnopqrstuvwxyz012345",
                                    },
                                    {"kind": "entry", "title": "Noise", "body": "thanks"},
                                ]
                            )
                        }
                    }
                ]
            }

    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_Resp())
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    with patch("memchentic.services.memory_ingest.httpx.AsyncClient", return_value=mock_client):
        rows = await memory_ingest.suggest_candidates("x", use_llm=True)
    get_settings.cache_clear()
    assert rows
    assert all(r["kind"] in {"entry", "guide"} for r in rows)
    blob = json.dumps(rows)
    assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in blob
    assert not any(r["body"].strip().lower() == "thanks" for r in rows)


@pytest.mark.asyncio
async def test_llm_http_error_falls_back_to_heuristic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEMORY_LLM_BASE_URL", "https://llm.example.test/v1")
    from memchentic.settings import get_settings

    get_settings.cache_clear()
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(side_effect=httpx.HTTPError("boom"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    with patch("memchentic.services.memory_ingest.httpx.AsyncClient", return_value=mock_client):
        fallback = await memory_ingest.suggest_candidates(
            "## Policy\n\nWhen identity is unsure, always confirm before changing billing.\n",
            use_llm=True,
        )
    get_settings.cache_clear()
    assert fallback
    assert fallback[0]["title"] == "Policy"
