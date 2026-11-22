"""Outbound email via SMTP (best-effort; no-op when mail is not configured)."""

from __future__ import annotations

import logging
from email.message import EmailMessage

import aiosmtplib
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.services.instance_settings import get_mail_settings, is_mail_configured
from memchentic.settings import get_settings

logger = logging.getLogger("memchentic")

__all__ = [
    "is_mail_configured",
    "send_api_key_notice",
    "send_email",
    "send_group_invite",
    "send_registration_confirm",
    "send_test_email",
]


async def send_email(
    session: AsyncSession,
    *,
    to: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> bool:
    """Send one message when SMTP is configured; otherwise no-op.

    Failures are logged and return False; they never raise to the caller.

    Args:
        session: Database session (for mail settings).
        to: Recipient address.
        subject: Subject line.
        text_body: Plain-text body.
        html_body: Optional HTML alternative.

    Returns:
        True when the message was accepted by the SMTP server.
    """
    mail = await get_mail_settings(session)
    if not mail.configured:
        return False
    message = EmailMessage()
    message["From"] = mail.from_address
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text_body)
    if html_body:
        message.add_alternative(html_body, subtype="html")
    try:
        await aiosmtplib.send(
            message,
            hostname=mail.host,
            port=mail.port,
            username=mail.username or None,
            password=mail.password or None,
            start_tls=mail.use_tls,
        )
        return True
    except Exception:
        logger.exception("Failed to send email to %s (subject=%r)", to, subject)
        return False


async def send_group_invite(
    session: AsyncSession,
    *,
    to: str,
    group_name: str,
    inviter_name: str,
    inviter_email: str | None,
) -> bool:
    """Notify an existing user that they were invited to a group."""
    base = get_settings().base_url.rstrip("/")
    groups_url = f"{base}/groups"
    inviter = inviter_name or inviter_email or "A teammate"
    if inviter_email and inviter_name:
        inviter = f"{inviter_name} ({inviter_email})"
    subject = f'Invitation to group "{group_name}"'
    text = (
        f'{inviter} invited you to the group "{group_name}" on memchentic.\n\n'
        f"Sign in and accept the invitation here:\n{groups_url}\n"
    )
    return await send_email(session, to=to, subject=subject, text_body=text)


async def send_registration_confirm(
    session: AsyncSession,
    *,
    to: str,
    token: str,
) -> bool:
    """Send a registration confirmation link."""
    base = get_settings().base_url.rstrip("/")
    link = f"{base}/verify-email?token={token}"
    subject = "Confirm your memchentic email"
    text = (
        "Thanks for registering with memchentic.\n\n"
        f"Confirm your email address by opening this link:\n{link}\n\n"
        "If you did not create an account, you can ignore this message.\n"
    )
    return await send_email(session, to=to, subject=subject, text_body=text)


async def send_api_key_notice(
    session: AsyncSession,
    *,
    to: str,
    action: str,
) -> bool:
    """Notify the key owner that a key was created or cycled (no secret).

    Args:
        session: Database session.
        to: Owner email.
        action: Short verb phrase, e.g. ``created`` or ``cycled``.
    """
    base = get_settings().base_url.rstrip("/")
    keys_url = f"{base}/api-keys"
    subject = f"Your memchentic API key was {action}"
    text = (
        f"A memchentic API key for this account was {action}.\n\n"
        "The secret is shown only once in the web UI and is never emailed.\n"
        f"If you did not expect this, revoke keys at:\n{keys_url}\n"
    )
    return await send_email(session, to=to, subject=subject, text_body=text)


async def send_test_email(session: AsyncSession, *, to: str) -> bool:
    """Send a short test message; raises on failure so the admin can fix config.

    Unlike :func:`send_email`, this raises when SMTP is not configured or the
    send fails.
    """
    mail = await get_mail_settings(session)
    if not mail.configured:
        raise RuntimeError("Mail is not configured (SMTP host and From are required)")
    message = EmailMessage()
    message["From"] = mail.from_address
    message["To"] = to
    message["Subject"] = "memchentic mail test"
    message.set_content(
        "This is a test message from memchentic. If you received it, outbound mail is working.\n"
    )
    await aiosmtplib.send(
        message,
        hostname=mail.host,
        port=mail.port,
        username=mail.username or None,
        password=mail.password or None,
        start_tls=mail.use_tls,
    )
    return True
