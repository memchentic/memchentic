"""Public registration gate: REGISTER_ENABLED + System Settings."""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "")


def _admin_auth() -> dict[str, str]:
    if not ADMIN_KEY:
        pytest.skip("ADMIN_API_KEY not set")
    return {"Authorization": f"Bearer {ADMIN_KEY}"}


def test_provider_reports_register_enabled(client: TestClient) -> None:
    info = client.get("/api/provider").json()
    assert "register_enabled" in info
    assert isinstance(info["register_enabled"], bool)


def test_registration_setting_admin_only_and_gates_public_register(
    client: TestClient,
) -> None:
    admin = _admin_auth()
    email = f"reg-{uuid.uuid4().hex[:10]}@example.com"
    password = "password-ok-12"

    denied = client.get("/api/system/registration")
    assert denied.status_code == 401

    current = client.get("/api/system/registration", headers=admin)
    assert current.status_code == 200, current.text
    assert current.json()["enabled"] is True

    off = client.put(
        "/api/system/registration", headers=admin, json={"enabled": False}
    )
    assert off.status_code == 200, off.text
    assert off.json()["enabled"] is False
    assert client.get("/api/provider").json()["register_enabled"] is False

    blocked = client.post(
        "/api/auth/register",
        json={"email": email, "password": password},
    )
    assert blocked.status_code == 403, blocked.text

    # Admins can still create users while public registration is off.
    created = client.post(
        "/api/auth/register",
        headers=admin,
        json={"email": email, "password": password, "display_name": "Admin-made"},
    )
    assert created.status_code == 200, created.text

    on = client.put(
        "/api/system/registration", headers=admin, json={"enabled": True}
    )
    assert on.status_code == 200, on.text
    assert on.json()["enabled"] is True

    other = f"reg2-{uuid.uuid4().hex[:10]}@example.com"
    ok = client.post(
        "/api/auth/register",
        json={"email": other, "password": password},
    )
    assert ok.status_code == 200, ok.text
