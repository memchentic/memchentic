"""ACL-scoped memory metrics aggregates."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import Principal
from memchentic.db.session import set_rls_context
from memchentic.models.entities import EntryType, Memory, MemoryKind
from memchentic.services.metrics import memory_metrics
from memchentic.services.spaces import get_personal_space
from memchentic.services.users import register_internal_user


def _user(client: TestClient) -> tuple[str, dict]:
    email = f"t{uuid.uuid4().hex[:10]}@example.com"
    password = f"pw-{uuid.uuid4().hex}"
    client.post("/api/auth/register", json={"email": email, "password": password})
    token = client.post("/api/auth/login", json={"email": email, "password": password}).json()[
        "access_token"
    ]
    me = client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).json()
    return token, me


def test_metrics_counts_and_acl(client: TestClient) -> None:
    """Metrics count own corpus and hide another user's personal memories."""
    token_a, me_a = _user(client)
    token_b, _me_b = _user(client)
    auth_a = {"Authorization": f"Bearer {token_a}"}
    auth_b = {"Authorization": f"Bearer {token_b}"}
    marker = uuid.uuid4().hex[:8]
    soon = (datetime.now(UTC) + timedelta(days=7)).isoformat()

    group = client.post("/api/spaces", headers=auth_a, json={"name": f"g-{marker}"}).json()

    client.post(
        "/api/memories",
        headers=auth_a,
        json={
            "title": f"fact {marker}",
            "body": f"body {marker}",
            "type": "fact",
            "tags": ["alpha", "shared"],
            "force": True,
        },
    )
    client.post(
        "/api/memories",
        headers=auth_a,
        json={
            "title": f"note {marker}",
            "body": f"body {marker}",
            "type": "note",
            "tags": ["alpha"],
            "expires_at": soon,
            "space_id": group["id"],
            "force": True,
        },
    )
    client.post(
        "/api/guides",
        headers=auth_a,
        json={
            "title": f"guide {marker}",
            "body": f"When X do Y {marker}",
            "tags": ["beta"],
            "space_id": me_a["personal_space_id"],
        },
    )
    client.post(
        "/api/memories",
        headers=auth_b,
        json={
            "title": f"secret {marker}",
            "body": f"other user {marker}",
            "type": "insight",
            "tags": ["secret"],
            "force": True,
        },
    )

    empty_b = client.get("/api/metrics/memories?range=30d", headers=auth_b).json()
    # User B only sees their own secret entry (1), not A's three.
    assert empty_b["totals"]["memories"] == 1
    assert empty_b["totals"]["entries"] == 1
    assert empty_b["totals"]["guides"] == 0
    assert all(row["tag"] != "alpha" for row in empty_b["top_tags"])

    metrics = client.get("/api/metrics/memories?range=30d", headers=auth_a).json()
    assert metrics["range"] == "30d"
    assert metrics["totals"]["memories"] == 3
    assert metrics["totals"]["entries"] == 2
    assert metrics["totals"]["guides"] == 1
    assert metrics["totals"]["expiring_soon"] == 1
    assert metrics["totals"]["spaces"] == 2

    kinds = {row["kind"]: row["count"] for row in metrics["by_kind"]}
    assert kinds.get("entry") == 2
    assert kinds.get("guide") == 1

    types = {row["type"]: row["count"] for row in metrics["by_type"]}
    assert types.get("fact") == 1
    assert types.get("note") == 1
    assert "guide" not in types

    tags = {row["tag"]: row["count"] for row in metrics["top_tags"]}
    assert tags.get("alpha") == 2
    assert tags.get("shared") == 1
    assert tags.get("beta") == 1
    assert "secret" not in tags

    space_names = {row["name"] for row in metrics["by_space"]}
    assert group["name"] in space_names

    creators = metrics["top_creators"]
    assert creators
    assert creators[0]["user_id"] == me_a["id"]
    assert creators[0]["count"] == 3
    assert creators[0]["email"]

    day_total = sum(row["entries"] + row["guides"] for row in metrics["created_per_day"])
    assert day_total == 3
    assert len(metrics["created_per_day"]) == 30

    personal = client.get(
        "/api/metrics/memories?range=7d&space_scope=personal",
        headers=auth_a,
    ).json()
    assert personal["totals"]["memories"] == 2
    assert personal["totals"]["guides"] == 1
    assert personal["totals"]["spaces"] == 1
    assert len(personal["created_per_day"]) == 7

    group_only = client.get(
        "/api/metrics/memories?range=all&space_scope=group",
        headers=auth_a,
    ).json()
    assert group_only["totals"]["memories"] == 1
    assert group_only["totals"]["entries"] == 1
    assert group_only["totals"]["guides"] == 0

    bad = client.get("/api/metrics/memories?range=1y", headers=auth_a)
    assert bad.status_code == 422


def test_metrics_empty_corpus(client: TestClient) -> None:
    """Fresh user with no memories gets zeroed charts, not an error."""
    token, _me = _user(client)
    auth = {"Authorization": f"Bearer {token}"}
    metrics = client.get("/api/metrics/memories", headers=auth).json()
    assert metrics["totals"]["memories"] == 0
    assert metrics["by_kind"] == []
    assert metrics["by_type"] == []
    assert metrics["top_tags"] == []
    assert metrics["top_creators"] == []
    assert len(metrics["created_per_day"]) == 30
    assert all(row["entries"] == 0 and row["guides"] == 0 for row in metrics["created_per_day"])


async def _user_with_entry(
    session: AsyncSession, created_at: datetime
) -> tuple[uuid.UUID, uuid.UUID]:
    """Create a user and one uncommitted entry in the personal space.

    Args:
        session: Admin-scoped test session.
        created_at: Creation time of the entry.

    Returns:
        ``(user id, personal space id)``.
    """
    user = await register_internal_user(
        session,
        email=f"metrics-{uuid.uuid4().hex[:10]}@example.com",
        password="password-ok-12",
    )
    # register_internal_user binds the RLS context to the new user.
    await set_rls_context(session, user_id=None, is_admin=True)
    personal = await get_personal_space(session, user.id)
    assert personal is not None
    session.add(
        Memory(
            space_id=personal.id,
            kind=MemoryKind.entry,
            entry_type=EntryType.note,
            title=f"metrics {uuid.uuid4().hex[:8]}",
            body="x",
            tags=["zeta", "alpha"],
            created_by_user_id=user.id,
            created_at=created_at,
            updated_at=created_at,
        )
    )
    await session.flush()
    return user.id, personal.id


async def test_metrics_statement_count_and_tag_order(session: AsyncSession) -> None:
    """Metrics use a bounded number of statements; tag ties sort by tag."""
    user_id, space_id = await _user_with_entry(session, datetime.now(UTC))
    statements: list[str] = []

    def before(*args: Any) -> None:
        statements.append(args[2])

    engine = session.get_bind().engine
    event.listen(engine, "before_cursor_execute", before)
    try:
        payload = await memory_metrics(
            session,
            Principal(is_admin=True, user_id=user_id),
            range_key="7d",
            space_id=space_id,
        )
    finally:
        event.remove(engine, "before_cursor_execute", before)
        await session.rollback()
    assert payload["totals"]["memories"] == 1
    assert payload["by_kind"] == [{"kind": "entry", "count": 1}]
    assert payload["top_tags"] == [
        {"tag": "alpha", "count": 1},
        {"tag": "zeta", "count": 1},
    ]
    # Up to 3 statements for the space check plus 6 aggregates.
    assert len(statements) <= 9


async def test_created_per_day_buckets_by_utc_day(session: AsyncSession) -> None:
    """Day buckets are UTC days even when the session time zone is not UTC."""
    yesterday = (datetime.now(UTC) - timedelta(days=1)).replace(
        hour=1, minute=0, second=0, microsecond=0
    )
    user_id, space_id = await _user_with_entry(session, yesterday)
    await session.execute(text("SET LOCAL TIME ZONE 'Asia/Tokyo'"))
    try:
        payload = await memory_metrics(
            session,
            Principal(is_admin=True, user_id=user_id),
            range_key="7d",
            space_id=space_id,
        )
    finally:
        await session.rollback()
    by_day = {row["day"]: row for row in payload["created_per_day"]}
    assert by_day[yesterday.date().isoformat()]["entries"] == 1
