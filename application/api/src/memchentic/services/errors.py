"""Typed errors raised by ``memchentic.services`` and mapped once for REST and MCP.

Every class also inherits the built-in exception it replaces (``ValueError``,
``LookupError`` or ``PermissionError``). Existing ``except`` clauses and
``pytest.raises`` checks written against the built-ins keep working.
"""

from __future__ import annotations

from typing import ClassVar


class ServiceError(Exception):
    """Base class for errors the caller can act on.

    Attributes:
        status_code: HTTP status used by the REST surface.
        code: Stable machine-readable code. REST returns it in the ``code``
            field; MCP uses it as the message prefix.
    """

    status_code: ClassVar[int] = 400
    code: ClassVar[str] = "error"

    def payload(self) -> dict[str, str]:
        """Return the JSON body for the REST surface.

        Returns:
            ``{"detail": <message>, "code": <code>}``.
        """
        return {"detail": str(self) or self.code, "code": self.code}


class ValidationError(ServiceError, ValueError):
    """A value in the request is malformed or not acceptable."""

    status_code = 422
    code = "invalid"


class NotFoundError(ServiceError, LookupError):
    """The resource does not exist or the caller is not allowed to see it."""

    status_code = 404
    code = "not_found"


class ForbiddenError(ServiceError, PermissionError):
    """The caller can see the resource but may not perform the action."""

    status_code = 403
    code = "forbidden"


class ConflictError(ServiceError, ValueError):
    """The request conflicts with the current state of a resource."""

    status_code = 409
    code = "conflict"


class NearDuplicateError(ConflictError):
    """An entry with near-identical content already exists in the target space.

    Attributes:
        duplicate_id: Id of the existing memory, when known.
    """

    code = "duplicate"

    def __init__(self, message: str, *, duplicate_id: str | None = None) -> None:
        """Store the message and the id of the existing memory.

        Args:
            message: Human-readable refusal.
            duplicate_id: Id of the existing memory.
        """
        super().__init__(message)
        self.duplicate_id = duplicate_id

    def payload(self) -> dict[str, str]:
        """Return the REST body, with ``duplicate_id`` when it is known.

        Returns:
            ``{"detail": ..., "code": "duplicate"}`` plus ``duplicate_id``.
        """
        body = super().payload()
        if self.duplicate_id:
            body["duplicate_id"] = self.duplicate_id
        return body


def http_error(exc: ServiceError) -> tuple[int, dict[str, str]]:
    """Map a service error to the REST status code and JSON body.

    This is the only place where a service error becomes an HTTP response.

    Args:
        exc: Error raised by a service function.

    Returns:
        ``(status_code, body)``.
    """
    return exc.status_code, exc.payload()


def tool_error_text(exc: ServiceError) -> str:
    """Map a service error to the MCP tool error text.

    This is the only place where a service error becomes MCP error text.

    Args:
        exc: Error raised by a service function.

    Returns:
        ``"<code>: <message>"``, for example ``"not_found: Memory not found"``.
    """
    return f"{exc.code}: {str(exc) or exc.code}"
