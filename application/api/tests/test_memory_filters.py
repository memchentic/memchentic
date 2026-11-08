"""Browse/search filters for space scope and multi-type selection."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient


def _user(client: TestClient) -> tuple[str, dict]:
    email = f"t{uuid.uuid4().hex[:10]}@example.com"
    password = f"pw-{uuid.uuid4().hex}"
    client.post("/api/auth/register", json={"email": email, "password": password})
    token = client.post("/api/auth/login", json={"email": email, "password": password}).json()[
        "access_token"
    ]
    auth = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/me", headers=auth).json()
    return token, me


def test_browse_space_scope_and_types(client: TestClient) -> None:
    """space_scope and types narrow browse results without client-side filtering."""
    token, me = _user(client)
    auth = {"Authorization": f"Bearer {token}"}
    marker = uuid.uuid4().hex[:8]

    group = client.post("/api/spaces", headers=auth, json={"name": f"g-{marker}"}).json()

    personal_fact = client.post(
        "/api/memories",
        headers=auth,
        json={
            "title": f"personal fact {marker}",
            "body": f"body {marker}",
            "type": "fact",
            "force": True,
        },
    ).json()
    personal_note = client.post(
        "/api/memories",
        headers=auth,
        json={
            "title": f"personal note {marker}",
            "body": f"body {marker}",
            "type": "note",
            "force": True,
        },
    ).json()
    group_pref = client.post(
        "/api/memories",
        headers=auth,
        json={
            "title": f"group pref {marker}",
            "body": f"body {marker}",
            "type": "preference",
            "space_id": group["id"],
            "force": True,
        },
    ).json()
    guide = client.post(
        "/api/guides",
        headers=auth,
        json={
            "title": f"guide {marker}",
            "body": f"When X do Y {marker}",
            "space_id": me["personal_space_id"],
        },
    ).json()

    assert personal_fact["created_by"] == me["id"]
    assert personal_fact["created_by_email"]
    owned = next(row for row in client.get("/api/memories", headers=auth).json() if row["id"] == personal_fact["id"])
    assert owned["created_by"] == me["id"]
    assert owned["created_by_email"] == personal_fact["created_by_email"]

    personal = client.get("/api/memories?space_scope=personal", headers=auth).json()
    personal_ids = {row["id"] for row in personal}
    assert personal_fact["id"] in personal_ids
    assert personal_note["id"] in personal_ids
    assert guide["id"] in personal_ids
    assert group_pref["id"] not in personal_ids

    groups = client.get("/api/memories?space_scope=group", headers=auth).json()
    group_ids = {row["id"] for row in groups}
    assert group_pref["id"] in group_ids
    assert personal_fact["id"] not in group_ids

    facts_only = client.get("/api/memories?types=fact", headers=auth).json()
    fact_ids = {row["id"] for row in facts_only}
    assert personal_fact["id"] in fact_ids
    assert personal_note["id"] not in fact_ids
    assert guide["id"] not in fact_ids

    mixed = client.get("/api/memories?types=note,guide", headers=auth).json()
    mixed_ids = {row["id"] for row in mixed}
    assert personal_note["id"] in mixed_ids
    assert guide["id"] in mixed_ids
    assert personal_fact["id"] not in mixed_ids


def test_search_types_include_guides(client: TestClient) -> None:
    """Search respects multi-type tokens including guides."""
    token, _me = _user(client)
    auth = {"Authorization": f"Bearer {token}"}
    marker = uuid.uuid4().hex[:8]

    client.post(
        "/api/memories",
        headers=auth,
        json={
            "title": f"entry {marker}",
            "body": f"unique token {marker} for search",
            "type": "insight",
            "force": True,
        },
    )
    client.post(
        "/api/guides",
        headers=auth,
        json={
            "title": f"guide {marker}",
            "body": f"unique token {marker} for search guide path",
        },
    )

    both = client.get(
        f"/api/memories/search?q={marker}&types=insight,guide",
        headers=auth,
    ).json()
    kinds = {row["kind"] for row in both}
    assert "entry" in kinds
    assert "guide" in kinds

    guides = client.get(
        f"/api/memories/search?q={marker}&types=guide",
        headers=auth,
    ).json()
    assert guides
    assert all(row["kind"] == "guide" for row in guides)
