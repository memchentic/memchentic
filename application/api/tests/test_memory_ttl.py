"""Sliding TTL: create-time defaults, recall renewal, debounce, ceiling, usage rank."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from memchentic.models.entities import EntryType, Memory, MemoryKind
from memchentic.search.hybrid import rerank_score
from memchentic.services.memory_ttl import resolve_expiry, slide_expiry


def _user(client: TestClient) -> dict[str, str]:
    email = f"t{uuid.uuid4().hex[:10]}@example.com"
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


def _days_from_now(iso: str | None) -> float | None:
    if iso is None:
        return None
    return (datetime.fromisoformat(iso) - datetime.now(UTC)).total_seconds() / 86400.0


def _create(client: TestClient, auth: dict[str, str], **extra: Any) -> dict[str, Any]:
    marker = uuid.uuid4().hex[:12]
    payload = {
        "title": f"ttl {marker}",
        "body": f"sliding ttl {marker}",
        "type": "fact",
        "force": True,
        **extra,
    }
    resp = client.post("/api/memories", headers=auth, json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _listed(client: TestClient, auth: dict[str, str], memory_id: str) -> dict[str, Any]:
    """Read a row through browse, which does not count as a recall."""
    rows = client.get(
        "/api/memories", headers=auth, params={"include_expired": "true", "limit": 100}
    ).json()
    return next(r for r in rows if r["id"] == memory_id)


def test_create_defaults_to_90_days_and_honors_overrides(client: TestClient) -> None:
    auth = _user(client)
    assert round(_days_from_now(_create(client, auth)["expires_at"])) == 90
    assert round(_days_from_now(_create(client, auth, ttl_days=7)["expires_at"])) == 7
    assert _create(client, auth, ttl_days=0)["expires_at"] is None
    assert _create(client, auth, permanent=True)["expires_at"] is None
    explicit = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    assert round(_days_from_now(_create(client, auth, expires_at=explicit)["expires_at"])) == 3
    bad = client.post(
        "/api/memories",
        headers=auth,
        json={"title": "neg", "body": "neg", "ttl_days": -1, "force": True},
    )
    assert bad.status_code == 422


def test_mcp_remember_and_guide_write_accept_ttl(client: TestClient) -> None:
    auth = _user(client)
    marker = uuid.uuid4().hex[:12]
    entry = _tool(
        client, auth, "remember", {"title": f"m {marker}", "body": f"mcp {marker}", "ttl_days": 14}
    )
    assert round(_days_from_now(entry["expires_at"])) == 14
    guide = _tool(client, auth, "guide_write", {"title": f"g {marker}", "body": f"when {marker}"})
    assert round(_days_from_now(guide["expires_at"])) == 90
    standing = _tool(
        client,
        auth,
        "guide_write",
        {"title": f"p {marker}", "body": f"always {uuid.uuid4().hex}", "permanent": True},
    )
    assert standing["expires_at"] is None


def test_open_slides_expiry_debounces_and_keeps_updated_at(client: TestClient) -> None:
    auth = _user(client)
    created = _create(client, auth, ttl_days=5)
    opened = client.get(
        f"/api/memories/{created['id']}", headers=auth, params={"touch": "true"}
    ).json()
    assert round(_days_from_now(opened["expires_at"])) == 90
    assert opened["recall_count"] == 1
    assert opened["last_recalled_at"] is not None
    assert opened["updated_at"] == created["updated_at"]

    again = client.get(
        f"/api/memories/{created['id']}", headers=auth, params={"touch": "true"}
    ).json()
    assert again["recall_count"] == 1


def test_find_hits_renew_but_expired_rows_stay_expired(client: TestClient) -> None:
    auth = _user(client)
    marker = uuid.uuid4().hex[:12]
    live = _create(client, auth, title=f"live {marker}", body=f"renew {marker}", ttl_days=2)
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    dead = _create(client, auth, title=f"dead {marker}", body=f"renew {marker}", expires_at=past)

    hits = _tool(client, auth, "find", {"query": marker, "include_expired": True})["result"]
    assert {live["id"], dead["id"]} <= {h["id"] for h in hits}

    live_row = _listed(client, auth, live["id"])
    assert live_row["recall_count"] == 1
    assert round(_days_from_now(live_row["expires_at"])) == 90
    dead_row = _listed(client, auth, dead["id"])
    assert dead_row["recall_count"] == 1
    assert dead_row["expires_at"] == dead["expires_at"]


def test_guide_reinforcement_renews_and_counts(client: TestClient) -> None:
    auth = _user(client)
    marker = uuid.uuid4().hex[:12]
    args = {"title": f"g {marker}", "body": f"reinforce {marker}", "ttl_days": 3}
    first = _tool(client, auth, "guide_write", args)
    second = _tool(client, auth, "guide_write", {"title": args["title"], "body": args["body"]})
    assert second["id"] == first["id"]
    assert second["confidence"] == 2.0
    assert second["recall_count"] == 1
    assert round(_days_from_now(second["expires_at"])) == 90


def test_resolve_and_slide_expiry_rules() -> None:
    now = datetime.now(UTC)
    assert resolve_expiry(expires_at=None, ttl_days=None, permanent=False, now=now) == (
        now + timedelta(days=90)
    )
    assert resolve_expiry(expires_at=None, ttl_days=None, permanent=True, now=now) is None
    assert slide_expiry(None, created_at=now, now=now) is None

    far = now + timedelta(days=400)
    assert slide_expiry(far, created_at=now, now=now) == far

    old = now - timedelta(days=700)
    capped = slide_expiry(now + timedelta(days=1), created_at=old, now=now)
    assert capped == old + timedelta(days=730)


def test_rerank_rewards_recall_count() -> None:
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "space_id": uuid.uuid4(),
        "kind": MemoryKind.entry,
        "entry_type": EntryType.fact,
        "title": "t",
        "body": "b",
        "tags": [],
        "confidence": 1.0,
        "created_at": now,
        "updated_at": now,
    }
    cold = Memory(**base, recall_count=0)
    hot = Memory(**{**base, "id": uuid.uuid4()}, recall_count=20)
    assert rerank_score(hot, 0.02, now=now) > rerank_score(cold, 0.02, now=now)


def test_guide_confidence_is_capped(client: TestClient) -> None:
    """Repeating guide_write stops raising confidence at the cap."""
    auth = _user(client)
    marker = uuid.uuid4().hex[:12]
    args = {"title": f"cap {marker}", "body": f"cap body {marker}"}
    last = None
    for _ in range(8):
        last = _tool(client, auth, "guide_write", args)
    assert last is not None
    assert last["confidence"] == 5.0
    assert last["recall_count"] == 7


def test_listing_tools_and_rest_detail_do_not_renew(client: TestClient) -> None:
    """since / by_tag / about_path and a plain REST detail read are not recalls."""
    auth = _user(client)
    marker = uuid.uuid4().hex[:12]
    created = _create(
        client, auth, ttl_days=5, tags=[f"tag-{marker}"], path_hint=f"src/{marker}.py"
    )
    start = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()

    listed_since = _tool(client, auth, "since", {"since": start})["result"]
    listed_tag = _tool(client, auth, "by_tag", {"tags": [f"tag-{marker}"]})["result"]
    listed_path = _tool(client, auth, "about_path", {"path": f"src/{marker}.py"})["result"]
    for rows in (listed_since, listed_tag, listed_path):
        assert created["id"] in {r["id"] for r in rows}

    detail = client.get(f"/api/memories/{created['id']}", headers=auth)
    assert detail.status_code == 200, detail.text
    assert detail.json()["body"].startswith("sliding ttl")

    row = _listed(client, auth, created["id"])
    assert row["recall_count"] == 0
    assert row["last_recalled_at"] is None
    assert round(_days_from_now(row["expires_at"])) == 5


def test_about_path_blank_matches_nothing(client: TestClient) -> None:
    auth = _user(client)
    _create(client, auth)
    _create(client, auth)
    assert _tool(client, auth, "about_path", {"path": ""})["result"] == []
    assert _tool(client, auth, "about_path", {"path": "   "})["result"] == []


def test_mcp_open_still_renews(client: TestClient) -> None:
    auth = _user(client)
    created = _create(client, auth, ttl_days=5)
    opened = _tool(client, auth, "open", {"id": created["id"]})
    assert opened["recall_count"] == 1
    assert round(_days_from_now(opened["expires_at"])) == 90


def test_rest_search_renews_unless_touch_is_false(client: TestClient) -> None:
    auth = _user(client)
    marker = uuid.uuid4().hex[:12]
    created = _create(client, auth, title=f"srch {marker}", body=f"search {marker}", ttl_days=5)

    quiet = client.get("/api/memories/search", headers=auth, params={"q": marker, "touch": "false"})
    assert quiet.status_code == 200, quiet.text
    assert created["id"] in {r["id"] for r in quiet.json()}
    assert _listed(client, auth, created["id"])["recall_count"] == 0

    counted = client.get("/api/memories/search", headers=auth, params={"q": marker})
    assert created["id"] in {r["id"] for r in counted.json()}
    row = _listed(client, auth, created["id"])
    assert row["recall_count"] == 1
    assert round(_days_from_now(row["expires_at"])) == 90
