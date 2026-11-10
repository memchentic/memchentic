"""Per-request principal and database session for MCP tools."""

from __future__ import annotations

from contextvars import ContextVar

from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import Principal

principal_var: ContextVar[Principal | None] = ContextVar("principal", default=None)
session_var: ContextVar[AsyncSession | None] = ContextVar("session", default=None)


def require_principal() -> Principal:
    """Return the request principal.

    Raises:
        PermissionError: When the request is not authenticated.
    """
    principal = principal_var.get()
    if principal is None:
        raise PermissionError("Unauthorized")
    return principal


def require_session() -> AsyncSession:
    """Return the request database session.

    Raises:
        RuntimeError: When no session is bound to the request.
    """
    session = session_var.get()
    if session is None:
        raise RuntimeError("No database session in context")
    return session
