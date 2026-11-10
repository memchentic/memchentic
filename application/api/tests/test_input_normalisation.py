"""Blank values, naive datetimes and tag normalisation (SEC-053)."""

from __future__ import annotations

import importlib.util
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from memchentic.services.errors import ValidationError
from memchentic.services.validation import (
    check_body,
    check_name,
    check_tags,
    check_title,
    normalise_tags,
    parse_datetime,
)

_MIGRATION = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "022_normalise_memory_tags.py"
)

PREFIX = "in"


def _user(client: TestClient) -> dict[str, str]:
    """Register and sign in a fresh user; return the auth headers."""
    email = f"{PREFIX}{uuid.uuid4().hex[:10]}@example.com"
    password = f"pw-{uuid.uuid4().hex}"
    assert (
        client.post("/api/auth/register", json={"email": email, "password": password}).status_code
        == 200
    )
    token = client.post("/api/auth/login", json={"email": email, "password": password}).json()[
        "access_token"
    ]
    return {"Authorization": f"Bearer {token}"}


def _tool(client: TestClient, auth: dict[str, str], name: str, args: dict[str, Any]) -> dict:
    """Call an MCP tool; return the JSON-RPC ``result`` object."""
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
    return resp.json()["result"]


def _rows(result: dict) -> list[dict[str, Any]]:
    content = result["structuredContent"]
    return content if isinstance(content, list) else content.get("result", content)


@pytest.fixture
def process_tz_utc_minus_5(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Run one test with the process time zone set to UTC-5."""
    monkeypatch.setenv("TZ", "EST5")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_validators_reject_blank_values() -> None:
    assert check_title("  ok  ") == "ok"
    assert check_body("  kept as is  ") == "  kept as is  "
    for call in (
        lambda: check_title("   "),
        lambda: check_body(""),
        lambda: check_body(" \n\t"),
        lambda: check_name("   ", "name", 255),
    ):
        with pytest.raises(ValidationError):
            call()


def test_tags_are_normalised() -> None:
    assert check_tags(["Docker", "docker", " docker", ""]) == ["docker"]
    assert check_tags(["B", "a", "b "]) == ["b", "a"]
    assert check_tags(None) == []


def test_migration_normalises_like_the_service() -> None:
    spec = importlib.util.spec_from_file_location("tag_migration", _MIGRATION)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for sample in (["Docker", "docker", " docker", ""], ["B", "a", "b "], [], ["  "]):
        assert module._normalise(sample) == normalise_tags(sample)


def test_naive_datetime_is_read_as_utc() -> None:
    parsed = parse_datetime("2031-01-01T00:00:00", "expires_at")
    assert parsed == datetime(2031, 1, 1, tzinfo=UTC)
    assert parsed is not None
    assert parsed.tzinfo is not None
    aware = parse_datetime("2031-01-01T00:00:00+02:00", "expires_at")
    assert aware == datetime(2030, 12, 31, 22, tzinfo=UTC)


def test_rest_rejects_blank_values(client: TestClient) -> None:
    auth = _user(client)
    assert client.post("/api/spaces", headers=auth, json={"name": "   "}).status_code == 422
    for payload in (
        {"title": "", "body": "b", "force": True},
        {"title": "   ", "body": "b", "force": True},
        {"title": f"t {uuid.uuid4().hex}", "body": "", "force": True},
        {"title": f"t {uuid.uuid4().hex}", "body": " \n ", "force": True},
    ):
        resp = client.post("/api/memories", headers=auth, json=payload)
        assert resp.status_code == 422, (payload, resp.status_code, resp.text[:200])
    created = client.post(
        "/api/memories",
        headers=auth,
        json={"title": f"t {uuid.uuid4().hex}", "body": "b", "force": True},
    )
    assert created.status_code == 200, created.text
    blanked = client.patch(
        f"/api/memories/{created.json()['id']}", headers=auth, json={"title": "  "}
    )
    assert blanked.status_code == 422, blanked.text


def test_mcp_rejects_blank_values(client: TestClient) -> None:
    auth = _user(client)
    calls: list[tuple[str, dict[str, Any]]] = [
        ("space_create", {"name": "   "}),
        ("remember", {"title": " ", "body": "b", "force": True}),
        ("remember", {"title": f"t {uuid.uuid4().hex}", "body": "", "force": True}),
        ("guide_write", {"title": f"g {uuid.uuid4().hex}", "body": "  "}),
        ("by_tag", {"tags": ["", "  "]}),
    ]
    for name, args in calls:
        result = _tool(client, auth, name, args)
        assert result.get("isError") is True, (name, result)
        assert "invalid: " in result["content"][0]["text"], (name, result)


def test_naive_expires_at_is_stored_as_utc(
    client: TestClient, process_tz_utc_minus_5: None
) -> None:
    auth = _user(client)
    resp = client.post(
        "/api/memories",
        headers=auth,
        json={
            "title": f"naive {uuid.uuid4().hex}",
            "body": "b",
            "expires_at": "2031-01-01T00:00:00",
            "force": True,
        },
    )
    assert resp.status_code == 200, resp.text
    stored = datetime.fromisoformat(resp.json()["expires_at"])
    assert stored == datetime(2031, 1, 1, tzinfo=UTC)


def test_tags_are_stored_normalised_and_found_in_any_case(client: TestClient) -> None:
    auth = _user(client)
    marker = uuid.uuid4().hex[:10]
    created = client.post(
        "/api/memories",
        headers=auth,
        json={
            "title": f"tags {marker}",
            "body": "x",
            "tags": [f"Docker-{marker}", f"docker-{marker}", f" docker-{marker}", ""],
            "force": True,
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["tags"] == [f"docker-{marker}"]
    rows = _rows(_tool(client, auth, "by_tag", {"tags": [f"DOCKER-{marker}"]}))
    assert [row["title"] for row in rows] == [f"tags {marker}"]
