"""A wrong ENCRYPTION_KEY is a server fault, never an HTTP 400 (SEC-050)."""

from __future__ import annotations

import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.tokens import decrypt_private_key, encrypt_private_key, verify_signing_key
from memchentic.settings import get_settings

# Dummy value used only to simulate a mismatch; it protects nothing.
OTHER_ENCRYPTION_KEY = "a-different-encryption-key-0123456789abcdef"


def test_decrypt_with_wrong_key_is_a_runtime_error() -> None:
    """decrypt_private_key names ENCRYPTION_KEY and does not raise ValueError."""
    settings = get_settings()
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = encrypt_private_key(private_key, settings)
    assert decrypt_private_key(pem, settings).key_size == 2048
    wrong = settings.model_copy(update={"encryption_key": OTHER_ENCRYPTION_KEY})
    with pytest.raises(RuntimeError, match="ENCRYPTION_KEY"):
        decrypt_private_key(pem, wrong)


async def test_verify_signing_key_detects_a_wrong_encryption_key(session: AsyncSession) -> None:
    """The boot check passes with the real key and fails with another key."""
    settings = get_settings()
    await verify_signing_key(session, settings)
    wrong = settings.model_copy(update={"encryption_key": OTHER_ENCRYPTION_KEY})
    with pytest.raises(RuntimeError, match="ENCRYPTION_KEY"):
        await verify_signing_key(session, wrong)


def test_login_with_wrong_encryption_key_is_not_a_400(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A correct password must not produce a client error when the key is wrong."""
    email = f"sk{uuid.uuid4().hex[:10]}@example.com"
    password = f"pw-{uuid.uuid4().hex}"
    created = client.post("/api/auth/register", json={"email": email, "password": password})
    assert created.status_code == 200, created.text
    monkeypatch.setattr(get_settings(), "encryption_key", OTHER_ENCRYPTION_KEY)
    # The test client re-raises unhandled server exceptions (HTTP 500 in production).
    with pytest.raises(RuntimeError, match="ENCRYPTION_KEY"):
        client.post("/api/auth/login", json={"email": email, "password": password})


def test_no_global_value_error_handler(client: TestClient) -> None:
    """ValueError has no blanket mapping to HTTP 400."""
    assert ValueError not in client.app.exception_handlers


def test_malformed_redirect_uri_is_a_400(client: TestClient) -> None:
    """A URL that urlparse rejects is still a clean client error."""
    resp = client.post("/oauth/register", json={"redirect_uris": ["https://[bad"]})
    assert resp.status_code == 400
    assert resp.json()["detail"].startswith("Invalid redirect_uri")


def test_nul_in_spa_path_is_not_a_server_error(client: TestClient) -> None:
    """A NUL character in a static path does not raise."""
    resp = client.get("/x%00y")
    assert resp.status_code in (200, 404)
