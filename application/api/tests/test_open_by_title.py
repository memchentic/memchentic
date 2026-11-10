"""``open`` by title: live rows win over expired rows; ``kind`` disambiguates."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient

from support import call_tool, register_user

EXPIRED = "2000-01-01T00:00:00+00:00"


def _user(client: TestClient) -> dict[str, str]:
    return register_user(client).headers


def _tool(
    client: TestClient, auth: dict[str, str], name: str, args: dict[str, Any]
) -> dict[str, Any]:
    """Call an MCP tool and return the JSON-RPC ``result`` (may carry ``isError``)."""
    return call_tool(client, auth, name, args)


def _entry(client: TestClient, auth: dict[str, str], title: str, **fields: Any) -> str:
    resp = client.post(
        "/api/memories",
        headers=auth,
        json={"title": title, "body": "entry body", "type": "fact", "force": True, **fields},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_open_by_title_prefers_the_live_row(client: TestClient) -> None:
    auth = _user(client)
    title = f"Deploy checklist {uuid.uuid4().hex[:8]}"
    _entry(client, auth, title, expires_at=EXPIRED)
    live = _entry(client, auth, title)

    result = _tool(client, auth, "open", {"title": title})

    assert result.get("isError") is not True, result
    assert result["structuredContent"]["id"] == live


def test_open_by_title_kind_selects_the_entry_or_the_guide(client: TestClient) -> None:
    auth = _user(client)
    title = f"Shared title {uuid.uuid4().hex[:8]}"
    entry = _entry(client, auth, title)
    guide_resp = client.post(
        "/api/guides", headers=auth, json={"title": title, "body": "guide body"}
    )
    assert guide_resp.status_code == 200, guide_resp.text
    guide = guide_resp.json()["id"]

    ambiguous = _tool(client, auth, "open", {"title": title})
    assert ambiguous.get("isError") is True
    assert "pass id, space_id or kind" in str(ambiguous)

    as_guide = _tool(client, auth, "open", {"title": title, "kind": "guide"})
    assert as_guide["structuredContent"]["id"] == guide
    assert as_guide["structuredContent"]["kind"] == "guide"

    as_entry = _tool(client, auth, "open", {"title": title, "kind": "entry"})
    assert as_entry["structuredContent"]["id"] == entry


def test_open_by_title_returns_an_expired_row_when_no_live_row_exists(
    client: TestClient,
) -> None:
    auth = _user(client)
    title = f"Expired only {uuid.uuid4().hex[:8]}"
    expired = _entry(client, auth, title, expires_at=EXPIRED)

    result = _tool(client, auth, "open", {"title": title})

    assert result.get("isError") is not True, result
    assert result["structuredContent"]["id"] == expired
    # The read does not revive the row.
    assert result["structuredContent"]["expires_at"].startswith("2000-01-01")


def test_open_rejects_an_unknown_kind(client: TestClient) -> None:
    auth = _user(client)

    result = _tool(client, auth, "open", {"title": "anything", "kind": "memo"})

    assert result.get("isError") is True
    assert "kind must be 'entry' or 'guide'" in str(result)
