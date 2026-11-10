"""FastAPI dependencies for database sessions and request principals."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.bearer import resolve_bearer
from memchentic.auth.principal import Principal, bearer_token_from_header
from memchentic.db.session import get_session_factory, set_rls_context


async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session scoped to one request.

    Yields:
        An async SQLAlchemy session.
    """
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def get_principal(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
) -> Principal:
    """Resolve the caller from the Bearer header and apply the RLS context.

    Args:
        request: Incoming request.
        session: Request database session.

    Returns:
        The authenticated principal.

    Raises:
        HTTPException: 401 when no valid admin key, API key, or JWT is presented.
    """
    token = bearer_token_from_header(request.headers.get("Authorization"))
    principal = await resolve_bearer(session, token)
    if principal is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    await set_rls_context(session, user_id=principal.user_id, is_admin=principal.is_admin)
    return principal


async def get_optional_principal(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
) -> Principal | None:
    """Resolve the caller when present; anonymous callers get an empty RLS context.

    Args:
        request: Incoming request.
        session: Request database session.

    Returns:
        The principal, or None for anonymous requests.
    """
    try:
        return await get_principal(request, session)
    except HTTPException:
        await set_rls_context(session, user_id=None, is_admin=False)
        return None
