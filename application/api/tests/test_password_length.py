"""The 72-byte bcrypt limit is checked by the application (SEC-054)."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from memchentic.auth.passwords import (
    MAX_PASSWORD_BYTES,
    hash_password,
    password_too_long,
    require_bcrypt_length,
    verify_password,
)


def _email() -> str:
    return f"pl{uuid.uuid4().hex[:10]}@example.com"


def test_limit_is_counted_in_bytes() -> None:
    assert MAX_PASSWORD_BYTES == 72
    assert not password_too_long("x" * 72)
    assert password_too_long("x" * 73)
    assert not password_too_long("\u00e9" * 36)
    assert password_too_long("\u00e9" * 37)


def test_hash_password_refuses_long_input_with_own_message() -> None:
    with pytest.raises(ValueError, match="password is longer than 72 bytes"):
        hash_password("x" * 73)
    assert verify_password("x" * 72, hash_password("x" * 72))


def test_verify_password_never_matches_long_input() -> None:
    stored = hash_password("x" * 72)
    assert verify_password("x" * 73, stored) is False


def test_startup_check_names_the_setting() -> None:
    require_bcrypt_length("x" * 72, "ADMIN_PASSWORD")
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD is longer than 72 bytes"):
        require_bcrypt_length("x" * 73, "ADMIN_PASSWORD")


def test_register_rejects_long_password_with_reason(client: TestClient) -> None:
    for password in ("x" * 73, "\u00e9" * 40):
        resp = client.post("/api/auth/register", json={"email": _email(), "password": password})
        assert resp.status_code == 422, resp.text
        assert "at most 72 bytes" in resp.text


def test_register_accepts_password_at_the_limit(client: TestClient) -> None:
    email = _email()
    password = "x" * 72
    registered = client.post("/api/auth/register", json={"email": email, "password": password})
    assert registered.status_code == 200
    signed_in = client.post("/api/auth/login", json={"email": email, "password": password})
    assert signed_in.status_code == 200


def test_login_with_long_password_is_invalid_credentials(client: TestClient) -> None:
    email = _email()
    registered = client.post("/api/auth/register", json={"email": email, "password": "x" * 72})
    assert registered.status_code == 200
    resp = client.post("/api/auth/login", json={"email": email, "password": "x" * 73})
    assert resp.status_code == 401, resp.text
