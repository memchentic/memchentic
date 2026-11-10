from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.exceptions import InvalidTokenError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.models.entities import InternalSigningKey
from memchentic.settings import Settings, get_settings

ACCESS_TOKEN_SECONDS = 3600
_SIGNING_KEY_LOCK = 0x6D656D6B  # pg advisory lock id; serializes key creation across replicas


def encrypt_private_key(private_key: rsa.RSAPrivateKey, settings: Settings) -> str:
    """Serialize a signing key as PKCS#8 PEM encrypted under ``ENCRYPTION_KEY``.

    Args:
        private_key: RSA signing key.
        settings: App settings carrying ``ENCRYPTION_KEY``.

    Returns:
        Encrypted PEM text safe to store in Postgres.
    """
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(
            settings.encryption_key.encode()
        ),
    ).decode()


def decrypt_private_key(private_pem: str, settings: Settings) -> rsa.RSAPrivateKey:
    """Load a stored signing key encrypted by :func:`encrypt_private_key`.

    Args:
        private_pem: Encrypted PKCS#8 PEM from ``internal_signing_keys``.
        settings: App settings carrying ``ENCRYPTION_KEY``.

    Returns:
        The RSA signing key.

    Raises:
        ValueError: When ``ENCRYPTION_KEY`` does not match the stored key.
    """
    key = serialization.load_pem_private_key(
        private_pem.encode(), password=settings.encryption_key.encode()
    )
    if not isinstance(key, rsa.RSAPrivateKey):
        raise TypeError("Stored signing key is not RSA")
    return key


async def _active_signing_key(session: AsyncSession) -> InternalSigningKey | None:
    result = await session.execute(
        select(InternalSigningKey)
        .where(InternalSigningKey.active.is_(True))
        .order_by(InternalSigningKey.created_at)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def ensure_signing_key(session: AsyncSession) -> InternalSigningKey:
    """Return the oldest active signing key, creating one if none exists.

    Creation holds a transaction-scoped Postgres advisory lock so replicas
    starting together agree on a single key.

    Args:
        session: Database session.

    Returns:
        The active signing key.
    """
    key = await _active_signing_key(session)
    if key:
        return key

    await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _SIGNING_KEY_LOCK})
    key = await _active_signing_key(session)
    if key:
        await session.commit()
        return key

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = encrypt_private_key(private_key, get_settings())
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    key = InternalSigningKey(
        id=uuid.uuid4(),
        kid=secrets.token_urlsafe(8),
        private_pem=private_pem,
        public_pem=public_pem,
        active=True,
    )
    session.add(key)
    await session.commit()
    await session.refresh(key)
    return key


def _b64url_uint(val: int) -> str:
    length = (val.bit_length() + 7) // 8
    return base64.urlsafe_b64encode(val.to_bytes(length, "big")).rstrip(b"=").decode()


async def jwks(session: AsyncSession) -> dict:
    result = await session.execute(
        select(InternalSigningKey).where(InternalSigningKey.active.is_(True))
    )
    keys = []
    for row in result.scalars():
        public = serialization.load_pem_public_key(row.public_pem.encode())
        numbers = public.public_numbers()  # type: ignore[attr-defined]
        keys.append(
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": row.kid,
                "n": _b64url_uint(numbers.n),
                "e": _b64url_uint(numbers.e),
            }
        )
    return {"keys": keys}


async def issue_access_token(
    session: AsyncSession,
    *,
    subject: str,
    email: str | None = None,
    provider: str = "internal",
    extra: dict | None = None,
    settings: Settings | None = None,
    expires_seconds: int = ACCESS_TOKEN_SECONDS,
) -> str:
    settings = settings or get_settings()
    key = await ensure_signing_key(session)
    now = datetime.now(UTC)
    payload = {
        "iss": settings.session_issuer,
        "aud": settings.effective_audience,
        "sub": subject,
        "email": email,
        "provider": provider,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_seconds)).timestamp()),
    }
    if extra:
        payload.update(extra)
    private_key = decrypt_private_key(key.private_pem, settings)
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": key.kid})


async def _verify_session_token(session: AsyncSession, token: str, settings: Settings) -> dict:
    """Verify a memchentic-signed GUI/session access token.

    Args:
        session: Database session for the active signing key.
        token: Compact JWT.
        settings: App settings (session issuer and audience).

    Returns:
        Decoded claims.

    Raises:
        InvalidTokenError: When the key is unknown or the token is invalid.
    """
    kid = jwt.get_unverified_header(token).get("kid")
    result = await session.execute(
        select(InternalSigningKey).where(
            InternalSigningKey.kid == kid, InternalSigningKey.active.is_(True)
        )
    )
    key = result.scalar_one_or_none()
    if key is None:
        raise InvalidTokenError("Unknown signing key")
    return jwt.decode(
        token,
        key.public_pem,
        algorithms=["RS256"],
        audience=settings.effective_audience,
        issuer=settings.session_issuer,
        options={"require": ["exp", "sub"]},
    )


async def verify_access_token(
    session: AsyncSession,
    token: str,
    settings: Settings | None = None,
) -> dict:
    """Verify a Bearer JWT from a GUI session or an external IdP.

    Session tokens are always memchentic-signed with ``session_issuer``. When
    ``OAUTH_PROVIDER`` is external, IdP JWTs verified via JWKS are also
    accepted so MCP harness Authorize and web Sign-in share one deployment.

    Args:
        session: Database session.
        token: Compact JWT.
        settings: Optional settings override.

    Returns:
        Decoded claims.

    Raises:
        InvalidTokenError: When neither session nor IdP verification succeeds.
    """
    settings = settings or get_settings()

    try:
        return await _verify_session_token(session, token, settings)
    except Exception:
        # Fall through to IdP JWKS when external; re-raise for internal-only.
        if settings.oauth_provider == "internal":
            raise

    from memchentic.auth.providers import fetch_jwks_and_decode

    return await fetch_jwks_and_decode(token, settings)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)
