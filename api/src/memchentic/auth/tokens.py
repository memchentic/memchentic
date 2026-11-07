from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import JWTError, jwt
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.models.entities import InternalSigningKey
from memchentic.settings import Settings, get_settings

ACCESS_TOKEN_SECONDS = 3600
_SIGNING_KEY_LOCK = 0x6D656D6B  # pg advisory lock id; serializes key creation across replicas


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
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
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
    result = await session.execute(select(InternalSigningKey).where(InternalSigningKey.active.is_(True)))
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
        "iss": settings.effective_issuer,
        "aud": settings.effective_audience,
        "sub": subject,
        "email": email,
        "provider": provider,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_seconds)).timestamp()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, key.private_pem, algorithm="RS256", headers={"kid": key.kid})


async def verify_access_token(
    session: AsyncSession,
    token: str,
    settings: Settings | None = None,
) -> dict:
    settings = settings or get_settings()

    if settings.oauth_provider == "internal":
        kid = jwt.get_unverified_header(token).get("kid")
        result = await session.execute(
            select(InternalSigningKey).where(
                InternalSigningKey.kid == kid, InternalSigningKey.active.is_(True)
            )
        )
        key = result.scalar_one_or_none()
        if key is None:
            raise JWTError("Unknown signing key")
        return jwt.decode(
            token,
            key.public_pem,
            algorithms=["RS256"],
            audience=settings.effective_audience,
            issuer=settings.effective_issuer,
        )

    # External: JWKS URI
    from memchentic.auth.providers import fetch_jwks_and_decode

    return await fetch_jwks_and_decode(token, settings)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)
