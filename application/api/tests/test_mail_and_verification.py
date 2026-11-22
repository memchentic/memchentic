"""SMTP System Settings and conditional email verification."""

from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.bearer import resolve_bearer
from memchentic.auth.tokens import issue_access_token
from memchentic.services.email_verification import issue_verification_token
from memchentic.services.instance_settings import set_mail_settings
from memchentic.settings import get_settings

ADMIN_KEY = os.environ.get("ADMIN_API_KEY", "")


def _admin_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_KEY}"}


_ADMIN = type("P", (), {"is_admin": True, "email": "admin@example.com", "display_name": "Admin"})()


def test_mail_settings_admin_only_and_hides_password(client: TestClient) -> None:
    admin = _admin_auth()
    assert client.get("/api/system/mail").status_code == 401

    current = client.get("/api/system/mail", headers=admin)
    assert current.status_code == 200, current.text
    body = current.json()
    assert body["configured"] is False
    assert "password" not in body
    assert body["password_set"] is False

    with patch("memchentic.services.mail.aiosmtplib.send", new_callable=AsyncMock) as send:
        saved = client.put(
            "/api/system/mail",
            headers=admin,
            json={
                "host": "smtp.example.com",
                "port": 587,
                "username": "mailer",
                "password": "secret-pass",
                "from_address": "noreply@example.com",
                "use_tls": True,
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["configured"] is True
        assert saved.json()["password_set"] is True
        assert "password" not in saved.json()
        assert "secret-pass" not in saved.text
        send.assert_not_called()

    again = client.get("/api/system/mail", headers=admin)
    assert again.json()["password_set"] is True
    assert "password" not in again.json()


def test_register_without_mail_stays_usable(client: TestClient) -> None:
    email = f"nmail-{uuid.uuid4().hex[:10]}@example.com"
    password = "password-ok-12"
    created = client.post(
        "/api/auth/register",
        json={"email": email, "password": password},
    )
    assert created.status_code == 200, created.text
    assert created.json()["verification_required"] is False

    login = client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
        headers={"X-Memchentic-Client": "gui"},
    )
    assert login.status_code == 200, login.text
    assert "access_token" in login.json()


def test_register_with_mail_requires_verification(client: TestClient) -> None:
    admin = _admin_auth()
    email = f"vmail-{uuid.uuid4().hex[:10]}@example.com"
    password = "password-ok-12"
    raw_token: dict[str, str] = {}

    async def _capture_send(session, *, to, token):
        assert to == email
        raw_token["value"] = token
        return True

    with (
        patch("memchentic.services.mail.aiosmtplib.send", new_callable=AsyncMock),
        patch(
            "memchentic.services.email_verification.send_registration_confirm",
            new=_capture_send,
        ),
    ):
        cfg = client.put(
            "/api/system/mail",
            headers=admin,
            json={
                "host": "smtp.example.com",
                "from_address": "noreply@example.com",
                "password": "x",
            },
        )
        assert cfg.status_code == 200, cfg.text
        assert cfg.json()["configured"] is True

        created = client.post(
            "/api/auth/register",
            json={"email": email, "password": password, "display_name": "Verify Me"},
        )
        assert created.status_code == 200, created.text
        assert created.json()["verification_required"] is True

        blocked = client.post(
            "/api/auth/login",
            json={"email": email, "password": password},
            headers={"X-Memchentic-Client": "gui"},
        )
        assert blocked.status_code == 403, blocked.text
        assert "Confirm your email" in blocked.text

        assert raw_token.get("value"), "confirmation email was not sent"
        verified = client.post(
            "/api/auth/verify-email",
            json={"token": raw_token["value"]},
        )
        assert verified.status_code == 200, verified.text
        assert verified.json()["email"] == email

        ok = client.post(
            "/api/auth/login",
            json={"email": email, "password": password},
            headers={"X-Memchentic-Client": "gui"},
        )
        assert ok.status_code == 200, ok.text


def test_admin_register_skips_verification_when_mail_on(client: TestClient) -> None:
    admin = _admin_auth()
    email = f"adminmade-{uuid.uuid4().hex[:10]}@example.com"
    password = "password-ok-12"

    with patch("memchentic.services.mail.aiosmtplib.send", new_callable=AsyncMock):
        cfg = client.put(
            "/api/system/mail",
            headers=admin,
            json={"host": "smtp.example.com", "from_address": "noreply@example.com"},
        )
        assert cfg.status_code == 200, cfg.text

        created = client.post(
            "/api/auth/register",
            headers=admin,
            json={"email": email, "password": password, "display_name": "Admin Made"},
        )
        assert created.status_code == 200, created.text
        assert created.json()["verification_required"] is False

        login = client.post(
            "/api/auth/login",
            json={"email": email, "password": password},
            headers={"X-Memchentic-Client": "gui"},
        )
        assert login.status_code == 200, login.text


async def test_resolve_bearer_rejects_unverified_when_mail_on(
    session: AsyncSession,
) -> None:
    from memchentic.services.users import register_internal_user

    email = f"bearer-{uuid.uuid4().hex[:10]}@example.com"
    await set_mail_settings(
        session,
        _ADMIN,
        host="smtp.example.com",
        from_address="noreply@example.com",
    )
    with patch("memchentic.services.mail.aiosmtplib.send", new_callable=AsyncMock):
        user = await register_internal_user(
            session, email=email, password="password-ok-12", email_verified=False
        )
    assert user.email_verified is False
    token = await issue_access_token(
        session, subject=str(user.id), email=user.email, provider="internal"
    )
    principal = await resolve_bearer(session, token, get_settings())
    assert principal is None

    await set_mail_settings(session, _ADMIN, host="", from_address="")
    principal2 = await resolve_bearer(session, token, get_settings())
    assert principal2 is not None
    assert principal2.user_id == user.id


def test_mail_test_endpoint_requires_config(client: TestClient) -> None:
    admin = _admin_auth()
    failed = client.post("/api/system/mail/test", headers=admin)
    assert failed.status_code == 400, failed.text

    with patch("memchentic.services.mail.aiosmtplib.send", new_callable=AsyncMock) as send:
        cfg = client.put(
            "/api/system/mail",
            headers=admin,
            json={"host": "smtp.example.com", "from_address": "noreply@example.com"},
        )
        assert cfg.status_code == 200, cfg.text
        ok = client.post("/api/system/mail/test", headers=admin)
        assert ok.status_code == 200, ok.text
        assert ok.json()["status"] == "sent"
        send.assert_awaited()


async def test_issue_verification_token_consumes_prior(session: AsyncSession) -> None:
    from memchentic.services.users import register_internal_user

    email = f"tok-{uuid.uuid4().hex[:10]}@example.com"
    user = await register_internal_user(
        session, email=email, password="password-ok-12", email_verified=False
    )
    with patch(
        "memchentic.services.email_verification.send_registration_confirm",
        new_callable=AsyncMock,
        return_value=True,
    ):
        first = await issue_verification_token(session, user, send=True)
        second = await issue_verification_token(session, user, send=True)
        await session.commit()
    assert first != second


def test_verify_email_rejects_bad_token(client: TestClient) -> None:
    bad = client.post("/api/auth/verify-email", json={"token": "not-a-real-token"})
    assert bad.status_code == 400, bad.text


def test_resend_verification_is_silent(client: TestClient) -> None:
    admin = _admin_auth()
    email = f"resend-{uuid.uuid4().hex[:10]}@example.com"
    password = "password-ok-12"
    raw_token: dict[str, str] = {}

    async def _capture_send(session, *, to, token):
        raw_token["value"] = token
        return True

    with (
        patch("memchentic.services.mail.aiosmtplib.send", new_callable=AsyncMock),
        patch(
            "memchentic.services.email_verification.send_registration_confirm",
            new=_capture_send,
        ),
    ):
        assert (
            client.put(
                "/api/system/mail",
                headers=admin,
                json={"host": "smtp.example.com", "from_address": "noreply@example.com"},
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/auth/register",
                json={"email": email, "password": password},
            ).status_code
            == 200
        )
        first = raw_token.get("value")
        assert first
        raw_token.clear()
        resend = client.post("/api/auth/resend-verification", json={"email": email})
        assert resend.status_code == 200, resend.text
        assert raw_token.get("value")
        assert raw_token["value"] != first

    silent = client.post(
        "/api/auth/resend-verification",
        json={"email": f"missing-{uuid.uuid4().hex[:8]}@example.com"},
    )
    assert silent.status_code == 200, silent.text


async def test_mail_helpers_send_when_configured(session: AsyncSession) -> None:
    from memchentic.services import mail as mail_service

    await set_mail_settings(
        session,
        _ADMIN,
        host="smtp.example.com",
        from_address="noreply@example.com",
        password="secret",
    )
    with patch("memchentic.services.mail.aiosmtplib.send", new_callable=AsyncMock) as send:
        assert await mail_service.send_registration_confirm(
            session, to="a@example.com", token="tok"
        )
        assert await mail_service.send_group_invite(
            session,
            to="b@example.com",
            group_name="Team",
            inviter_name="Ada",
            inviter_email="ada@example.com",
        )
        assert await mail_service.send_api_key_notice(session, to="c@example.com", action="created")
        assert send.await_count == 3

    await set_mail_settings(session, _ADMIN, host="", from_address="")
    with patch("memchentic.services.mail.aiosmtplib.send", new_callable=AsyncMock) as send:
        assert (
            await mail_service.send_email(session, to="x@example.com", subject="s", text_body="b")
            is False
        )
        send.assert_not_called()

    await set_mail_settings(
        session,
        _ADMIN,
        host="smtp.example.com",
        from_address="noreply@example.com",
    )
    with patch(
        "memchentic.services.mail.aiosmtplib.send",
        new_callable=AsyncMock,
        side_effect=RuntimeError("smtp down"),
    ):
        assert (
            await mail_service.send_email(session, to="x@example.com", subject="s", text_body="b")
            is False
        )
