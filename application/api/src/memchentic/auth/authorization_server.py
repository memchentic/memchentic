"""OAuth authorization-server rules: redirect URIs, codes, PKCE, refresh rotation."""

from __future__ import annotations

import base64
import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.tokens import hash_token, issue_access_token, new_opaque_token
from memchentic.models.entities import AuthCode, AuthRefreshToken, OAuthClient
from memchentic.settings import Settings, get_settings

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_SCHEME_NAME = re.compile(r"^[a-z][a-z0-9+.-]*$")
MAX_REDIRECT_URI_LEN = 1024  # auth_codes.redirect_uri is String(1024)


class OAuthRequestError(Exception):
    """An OAuth request that the authorization server rejects with HTTP 400.

    Attributes:
        error: RFC 6749 section 5.2 error code, used by the token endpoint.
        description: Human-readable detail; also the message of the exception.
    """

    def __init__(self, description: str, *, error: str = "invalid_request") -> None:
        super().__init__(description)
        self.error = error
        self.description = description


@dataclass(frozen=True)
class TokenGrant:
    """Tokens issued by a successful grant."""

    access_token: str
    refresh_token: str
    scope: str


def validate_redirect_uri(uri: str) -> str:
    """Validate a redirect URI for dynamic client registration.

    Allows ``https://`` URLs without userinfo or fragment, and loopback
    ``http://`` only for hosts in {127.0.0.1, localhost, [::1], ::1}.
    Private-use schemes listed in ``OAUTH_NATIVE_REDIRECT_SCHEMES`` are
    accepted for native clients.

    Args:
        uri: Candidate redirect URI string.

    Returns:
        The same URI when valid.

    Raises:
        OAuthRequestError: When the URI is empty, not a string, too long,
            contains whitespace, control characters or a backslash, is
            relative, or uses a disallowed scheme/host/userinfo/fragment.
    """
    if not uri or not isinstance(uri, str) or not uri.strip():
        raise OAuthRequestError("Invalid redirect_uri: empty")
    if len(uri) > MAX_REDIRECT_URI_LEN:
        raise OAuthRequestError(
            f"Invalid redirect_uri: longer than {MAX_REDIRECT_URI_LEN} characters"
        )
    if any(ch.isspace() or ch == "\\" or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in uri):
        raise OAuthRequestError(
            "Invalid redirect_uri: whitespace, control characters and backslashes are not allowed",
        )
    try:
        parsed = urlparse(uri)
    except ValueError as exc:
        raise OAuthRequestError("Invalid redirect_uri: not a valid URL") from exc
    scheme = (parsed.scheme or "").lower()
    if not scheme:
        raise OAuthRequestError("Invalid redirect_uri: relative URIs are not allowed")
    if scheme in {"javascript", "data", "file"}:
        raise OAuthRequestError(f"Invalid redirect_uri: scheme {scheme!r} is not allowed")
    if parsed.fragment:
        raise OAuthRequestError("Invalid redirect_uri: fragment is not allowed")
    if parsed.username is not None or parsed.password is not None:
        raise OAuthRequestError("Invalid redirect_uri: userinfo is not allowed")
    if scheme == "https":
        if not parsed.hostname:
            raise OAuthRequestError("Invalid redirect_uri: https host required")
        return uri
    if scheme == "http":
        host = (parsed.hostname or "").lower()
        if host not in _LOOPBACK_HOSTS:
            raise OAuthRequestError(
                "Invalid redirect_uri: http is only allowed for loopback "
                "(127.0.0.1, localhost, ::1)",
            )
        return uri
    native_schemes = {
        name
        for name in (
            part.strip().lower() for part in get_settings().oauth_native_redirect_schemes.split(",")
        )
        if _SCHEME_NAME.match(name)
    }
    if scheme in native_schemes:
        if not (parsed.netloc or parsed.path):
            raise OAuthRequestError(
                "Invalid redirect_uri: a private-use URI needs a host or a path"
            )
        return uri
    raise OAuthRequestError(f"Invalid redirect_uri: scheme {scheme!r} is not allowed")


def verify_pkce(code_verifier: str, challenge: str, method: str) -> bool:
    if method != "S256":
        return False
    digest = hashlib.sha256(code_verifier.encode()).digest()
    calc = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return calc == challenge


async def revoke_refresh_family(session: AsyncSession, family_id: uuid.UUID) -> None:
    await session.execute(
        update(AuthRefreshToken)
        .where(
            AuthRefreshToken.family_id == family_id,
            AuthRefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC))
    )


async def issue_auth_code(
    session: AsyncSession,
    *,
    client_id: str,
    user_id: uuid.UUID,
    redirect_uri: str,
    scope: str,
    code_challenge: str,
    code_challenge_method: str,
) -> str:
    """Store a one-time authorization code and commit.

    Args:
        session: Database session.
        client_id: Registered OAuth client id.
        user_id: Authenticated user the code is issued for.
        redirect_uri: Redirect URI the code is bound to.
        scope: Granted scope string.
        code_challenge: PKCE challenge, or an empty string.
        code_challenge_method: PKCE method, or an empty string.

    Returns:
        The raw code to send to the client.
    """
    code = new_opaque_token()
    auth_code = AuthCode(
        id=uuid.uuid4(),
        code_hash=hash_token(code),
        client_id=client_id,
        user_id=user_id,
        redirect_uri=redirect_uri,
        scope=scope,
        code_challenge=code_challenge or None,
        code_challenge_method=code_challenge_method or None,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    session.add(auth_code)
    await session.commit()
    return code


async def redeem_auth_code(
    session: AsyncSession,
    *,
    code: str,
    client_id: str,
    redirect_uri: str | None,
    code_verifier: str | None,
    settings: Settings,
) -> TokenGrant:
    """Consume an authorization code once and issue the first token pair.

    Args:
        session: Database session.
        code: Raw authorization code from the client.
        client_id: Client id presented with the code.
        redirect_uri: Redirect URI presented with the code.
        code_verifier: PKCE verifier, if any.
        settings: Application settings.

    Returns:
        The access token, refresh token and scope.

    Raises:
        OAuthRequestError: The code, client, redirect URI or PKCE check failed.
    """
    result = await session.execute(
        text(
            """
            UPDATE auth_codes
            SET consumed_at = now()
            WHERE code_hash = :h
              AND consumed_at IS NULL
              AND expires_at > now()
            RETURNING id, client_id, user_id, redirect_uri, scope,
                      code_challenge, code_challenge_method
            """
        ),
        {"h": hash_token(code)},
    )
    row = result.mappings().first()
    if not row:
        await session.rollback()
        raise OAuthRequestError("Invalid code", error="invalid_grant")
    if row["client_id"] != client_id:
        await session.rollback()
        raise OAuthRequestError("client_id mismatch", error="invalid_grant")
    if row["redirect_uri"] != redirect_uri:
        await session.rollback()
        raise OAuthRequestError("redirect_uri mismatch", error="invalid_grant")
    client_row = await session.execute(
        select(OAuthClient).where(OAuthClient.client_id == client_id)
    )
    client = client_row.scalar_one_or_none()
    if not client:
        await session.rollback()
        raise OAuthRequestError("Unknown client_id", error="invalid_client")
    if not (
        row["code_challenge"]
        and code_verifier
        and verify_pkce(code_verifier, row["code_challenge"], row["code_challenge_method"] or "")
    ):
        await session.rollback()
        raise OAuthRequestError("PKCE verification failed", error="invalid_grant")
    subject, email_claim, provider = str(row["user_id"]), None, "internal"
    token_user_id = row["user_id"]
    access = await issue_access_token(
        session,
        subject=subject,
        email=email_claim,
        provider=provider,
        settings=settings,
    )
    refresh = new_opaque_token()
    family_id = uuid.uuid4()
    session.add(
        AuthRefreshToken(
            id=uuid.uuid4(),
            token_hash=hash_token(refresh),
            family_id=family_id,
            client_id=client_id,
            user_id=token_user_id,
            scope=row["scope"],
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
    )
    await session.commit()
    return TokenGrant(access_token=access, refresh_token=refresh, scope=row["scope"])


async def rotate_refresh_token(
    session: AsyncSession,
    *,
    refresh_token: str,
    client_id: str,
    settings: Settings,
) -> TokenGrant:
    """Consume a refresh token once and issue the next token pair.

    Args:
        session: Database session.
        refresh_token: Raw refresh token from the client.
        client_id: Client id presented with the token.
        settings: Application settings.

    Returns:
        The access token, the new refresh token and scope.

    Raises:
        OAuthRequestError: The token is unknown, expired, revoked or reused.
    """
    token_h = hash_token(refresh_token)
    # Peek first for reuse detection (already-revoked presented again).
    existing = (
        await session.execute(
            select(AuthRefreshToken).where(AuthRefreshToken.token_hash == token_h)
        )
    ).scalar_one_or_none()
    if existing is not None and existing.revoked_at is not None and existing.client_id == client_id:
        await revoke_refresh_family(session, existing.family_id)
        await session.commit()
        raise OAuthRequestError("Invalid refresh token", error="invalid_grant")

    # Atomic consume: only one concurrent refresh wins the row.
    consumed = (
        (
            await session.execute(
                text(
                    """
                UPDATE auth_refresh_tokens
                SET revoked_at = now()
                WHERE token_hash = :h
                  AND revoked_at IS NULL
                  AND expires_at > now()
                  AND client_id = :cid
                RETURNING id, family_id, user_id, scope
                """
                ),
                {"h": token_h, "cid": client_id},
            )
        )
        .mappings()
        .first()
    )
    if not consumed:
        await session.rollback()
        raise OAuthRequestError("Invalid refresh token", error="invalid_grant")

    new_refresh = new_opaque_token()
    session.add(
        AuthRefreshToken(
            id=uuid.uuid4(),
            token_hash=hash_token(new_refresh),
            family_id=consumed["family_id"],
            client_id=client_id,
            user_id=consumed["user_id"],
            scope=consumed["scope"],
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
    )
    access = await issue_access_token(
        session,
        subject=str(consumed["user_id"]),
        email=None,
        provider="internal",
        settings=settings,
    )
    await session.commit()
    return TokenGrant(access_token=access, refresh_token=new_refresh, scope=consumed["scope"])
