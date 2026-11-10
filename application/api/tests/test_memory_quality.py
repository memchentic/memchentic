"""Org-scale recall quality: expiry filter, re-rank, near-dup top-k, overlap."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from memchentic.models.entities import EntryType, Memory, MemoryKind
from memchentic.search.hybrid import rerank_score
from memchentic.services import memories as memory_service
from memchentic.settings import get_settings


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


def _memory(**kw: Any) -> Memory:
    now = datetime.now(UTC)
    defaults: dict[str, Any] = {
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
    defaults.update(kw)
    return Memory(**defaults)


def test_expired_hidden_by_default_and_listed_on_request(client: TestClient) -> None:
    """Past-due expires_at is excluded from browse/search unless include_expired."""
    auth = _user(client)
    marker = uuid.uuid4().hex[:10]
    group = client.post("/api/spaces", headers=auth, json={"name": f"g-{marker}"}).json()
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    ids = {}
    for label, expiry in (("expired", past), ("live", future), ("open", None)):
        payload = {
            "title": f"{label} {marker}",
            "body": f"quality {marker} {label}",
            "type": "fact",
            "space_id": group["id"],
            "force": True,
        }
        if expiry:
            payload["expires_at"] = expiry
        resp = client.post("/api/memories", headers=auth, json=payload)
        assert resp.status_code == 200, resp.text
        ids[label] = resp.json()["id"]

    browse = client.get("/api/memories", headers=auth, params={"space_id": group["id"]}).json()
    assert {m["id"] for m in browse} == {ids["live"], ids["open"]}
    browse_all = client.get(
        "/api/memories",
        headers=auth,
        params={"space_id": group["id"], "include_expired": "true"},
    ).json()
    assert ids["expired"] in {m["id"] for m in browse_all}

    hits = client.get(
        "/api/memories/search", headers=auth, params={"q": marker, "space_id": group["id"]}
    ).json()
    assert ids["expired"] not in {h["id"] for h in hits}
    hits_all = client.get(
        "/api/memories/search",
        headers=auth,
        params={"q": marker, "space_id": group["id"], "include_expired": "true"},
    ).json()
    assert ids["expired"] in {h["id"] for h in hits_all}

    found = _tool(client, auth, "find", {"query": marker, "space_id": group["id"]})["result"]
    assert ids["expired"] not in {d["id"] for d in found}
    found_all = _tool(
        client,
        auth,
        "find",
        {"query": marker, "space_id": group["id"], "include_expired": True},
    )["result"]
    assert ids["expired"] in {d["id"] for d in found_all}


def test_rerank_prefers_recent_rows() -> None:
    """Equal RRF scores rank a fresh row above a months-old one."""
    now = datetime.now(UTC)
    fresh = _memory(updated_at=now)
    stale = _memory(updated_at=now - timedelta(days=180))
    assert rerank_score(fresh, 0.03, now=now) > rerank_score(stale, 0.03, now=now)


def test_rerank_path_tag_and_guide_confidence() -> None:
    """Path-tagged rows and higher-confidence guides get boosted."""
    now = datetime.now(UTC)
    tagged = _memory(path_hint="application/api/src")
    untagged = _memory(path_hint=None)
    assert rerank_score(tagged, 0.03, now=now, path_hint="application/api") > rerank_score(
        untagged, 0.03, now=now, path_hint="application/api"
    )
    strong = _memory(kind=MemoryKind.guide, entry_type=None, confidence=5.0)
    weak = _memory(kind=MemoryKind.guide, entry_type=None, confidence=1.0)
    assert rerank_score(strong, 0.03, now=now) > rerank_score(weak, 0.03, now=now)


class _TitleResult:
    def __init__(self, hit: Memory | None) -> None:
        self._hit = hit

    def scalar_one_or_none(self) -> Memory | None:
        return self._hit


class _VecRows:
    def __init__(self, rows: list[tuple[Memory, float]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[Memory, float]]:
        return self._rows


class _Session:
    """First execute = title lookup; later executes = vector candidates."""

    def __init__(
        self,
        rows: list[tuple[Memory, float]],
        *,
        title_hit: Memory | None = None,
    ) -> None:
        self._rows = rows
        self._title_hit = title_hit
        self._calls = 0

    async def execute(self, _stmt: Any) -> _TitleResult | _VecRows:
        self._calls += 1
        if self._calls == 1:
            return _TitleResult(self._title_hit)
        return _VecRows(self._rows)


class _Embedder:
    async def aembed_memory_text(self, _title: str, _body: str) -> list[float]:
        return [0.0] * 384


@pytest.mark.asyncio
async def test_near_dup_checks_beyond_nearest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exact title match is found even when outside the nearest-vector window."""
    monkeypatch.setattr(memory_service, "get_embedder", lambda: _Embedder())
    nearest = _memory(title="Different title")
    twin = _memory(title="Deploy Runbook")
    session = _Session([(nearest, 0.30)], title_hit=twin)
    found = await memory_service.find_near_duplicate(
        session,  # type: ignore[arg-type]
        space_id=uuid.uuid4(),
        title="deploy runbook",
        body="x",
        kind=MemoryKind.entry,
    )
    assert found is twin


@pytest.mark.asyncio
async def test_near_dup_prefers_similarity_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A later candidate above the similarity threshold wins over no title match."""
    monkeypatch.setattr(memory_service, "get_embedder", lambda: _Embedder())
    far = _memory(title="a")
    close = _memory(title="b")
    session = _Session([(far, 0.5), (close, 0.01)], title_hit=None)
    found = await memory_service.find_near_duplicate(
        session,  # type: ignore[arg-type]
        space_id=uuid.uuid4(),
        title="c",
        body="x",
        kind=MemoryKind.entry,
    )
    assert found is close


def test_overlap_report_scans_past_old_window_and_flags_near_dup(client: TestClient) -> None:
    """Scan window defaults above 200; cosine near-dups are reported."""
    assert get_settings().overlap_scan_limit > 200
    auth = _user(client)
    marker = uuid.uuid4().hex[:10]
    group = client.post("/api/spaces", headers=auth, json={"name": f"g-{marker}"}).json()
    body = f"Postgres connection pool exhaustion fix {marker} raise max_connections"
    made = []
    for title in (f"Pool fix {marker}", f"Pool fix {marker} again", f"Pool fix {marker}"):
        resp = client.post(
            "/api/memories",
            headers=auth,
            json={
                "title": title,
                "body": body,
                "type": "insight",
                "space_id": group["id"],
                "force": True,
            },
        )
        assert resp.status_code == 200, resp.text
        made.append(resp.json()["id"])

    report = _tool(client, auth, "overlap_report", {"space_id": group["id"]})
    reasons = {p["reason"] for p in report["near_duplicates"]}
    assert "same_title" in reasons
    assert "near_dup" in reasons
    pair_ids = {frozenset((p["a"], p["b"])) for p in report["near_duplicates"]}
    assert len(pair_ids) == len(report["near_duplicates"])


def test_rerank_caps_guide_confidence() -> None:
    """Confidence above the cap does not buy more rank."""
    now = datetime.now(UTC)
    base = {"kind": MemoryKind.guide, "entry_type": None, "created_at": now, "updated_at": now}
    capped = _memory(confidence=5.0, **base)
    inflated = _memory(confidence=500.0, **base)
    weak = _memory(confidence=1.0, **base)
    assert rerank_score(inflated, 0.03, now=now) == pytest.approx(
        rerank_score(capped, 0.03, now=now)
    )
    assert rerank_score(capped, 0.03, now=now) > rerank_score(weak, 0.03, now=now)
