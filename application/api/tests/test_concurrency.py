"""Concurrency regressions: overlapping requests that the database must serialise.

The session-scoped ``client`` runs every request on one event loop, so requests
sent from a thread pool overlap inside the application.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient


def _user(client: TestClient) -> tuple[dict[str, str], str]:
    """Register and log in a fresh internal user.

    Args:
        client: In-process test client.

    Returns:
        Tuple of (Authorization header, user id).
    """
    email = f"c{uuid.uuid4().hex[:10]}@example.com"
    password = f"pw-{uuid.uuid4().hex}"
    client.post("/api/auth/register", json={"email": email, "password": password})
    token = client.post("/api/auth/login", json={"email": email, "password": password}).json()[
        "access_token"
    ]
    auth = {"Authorization": f"Bearer {token}"}
    return auth, client.get("/api/me", headers=auth).json()["id"]


def test_concurrent_identical_guide_writes_make_one_guide(client: TestClient) -> None:
    """Overlapping identical guide writes end as one guide, reinforced once per repeat."""
    auth, _ = _user(client)
    title = f"race guide {uuid.uuid4().hex[:8]}"
    payload = {"title": title, "body": "Pin base images by digest in every Dockerfile."}
    writers = 4

    def write(_: int) -> tuple[int, str]:
        resp = client.post("/api/guides", headers=auth, json=payload)
        return resp.status_code, resp.json()["id"]

    with ThreadPoolExecutor(writers) as pool:
        results = list(pool.map(write, range(writers)))

    assert {status for status, _ in results} == {200}
    assert len({guide_id for _, guide_id in results}) == 1
    rows = [
        m
        for m in client.get(
            "/api/memories", headers=auth, params={"kind": "guide", "limit": 100}
        ).json()
        if m["title"] == title
    ]
    assert len(rows) == 1
    assert rows[0]["confidence"] == float(writers)


def test_concurrent_identical_remember_makes_one_entry(client: TestClient) -> None:
    """Overlapping identical entry writes create one entry; the rest are refused."""
    auth, _ = _user(client)
    title = f"race entry {uuid.uuid4().hex[:8]}"
    payload = {
        "title": title,
        "body": "Always run the migration in a transaction and take an advisory lock first.",
    }

    def create(_: int) -> int:
        return client.post("/api/memories", headers=auth, json=payload).status_code

    with ThreadPoolExecutor(8) as pool:
        statuses = list(pool.map(create, range(8)))

    assert statuses.count(200) == 1
    rows = [
        m
        for m in client.get("/api/memories", headers=auth, params={"limit": 100}).json()
        if m["title"] == title
    ]
    assert len(rows) == 1


def test_concurrent_add_member_is_idempotent(client: TestClient) -> None:
    """Overlapping adds of the same user all succeed."""
    owner, _ = _user(client)
    _, member_id = _user(client)
    group = client.post(
        "/api/spaces", headers=owner, json={"name": f"race-{uuid.uuid4().hex[:6]}"}
    ).json()

    def add(_: int) -> int:
        return client.post(
            f"/api/spaces/{group['id']}/members", headers=owner, json={"user_id": member_id}
        ).status_code

    with ThreadPoolExecutor(8) as pool:
        statuses = list(pool.map(add, range(8)))

    assert set(statuses) == {200}


def test_concurrent_register_same_email_never_500(client: TestClient) -> None:
    """Overlapping registrations of one email: one succeeds, the rest get the generic 400."""
    email = f"dup{uuid.uuid4().hex[:8]}@example.com"

    def register(_: int) -> int:
        return client.post(
            "/api/auth/register",
            json={"email": email, "password": f"pw-{uuid.uuid4().hex}"},
        ).status_code

    with ThreadPoolExecutor(6) as pool:
        statuses = list(pool.map(register, range(6)))

    assert statuses.count(200) == 1
    assert set(statuses) == {200, 400}


def test_concurrent_cycle_leaves_one_active_key(client: TestClient) -> None:
    """Overlapping cycles end with exactly one active key."""
    auth, _ = _user(client)

    def cycle(_: int) -> int:
        return client.post("/api/api-keys/cycle", headers=auth, json={}).status_code

    with ThreadPoolExecutor(6) as pool:
        statuses = list(pool.map(cycle, range(6)))

    assert set(statuses) == {200}
    assert len(client.get("/api/api-keys", headers=auth).json()) == 1
