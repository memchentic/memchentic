"""Global mapping from service-layer exceptions to HTTP responses."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from memchentic.services.errors import ServiceError, http_error

logger = logging.getLogger("memchentic")


def register_exception_handlers(app: FastAPI) -> None:
    """Register every service-exception handler on the app.

    Args:
        app: The FastAPI application under construction.
    """

    @app.exception_handler(ServiceError)
    async def service_error(_request: Request, exc: ServiceError) -> JSONResponse:
        """Map a typed service error to its HTTP status and JSON body.

        Args:
            _request: Incoming request (unused).
            exc: The error raised by a service.

        Returns:
            JSON response with ``detail`` and ``code``.
        """
        status_code, body = http_error(exc)
        return JSONResponse(body, status_code=status_code)

    @app.exception_handler(PermissionError)
    async def untyped_permission_denied(_request: Request, exc: PermissionError) -> JSONResponse:
        """Map a bare ``PermissionError`` to HTTP 403 (safety net for untyped denials).

        An operating-system permission error (``errno`` set) is a server fault
        and is re-raised.

        Args:
            _request: Incoming request (unused).
            exc: The denial.

        Returns:
            JSON 403 response.

        Raises:
            PermissionError: When the error carries an ``errno``.
        """
        if exc.errno is not None:
            raise exc
        logger.warning("Untyped PermissionError reached the HTTP layer: %s", exc)
        return JSONResponse(
            {"detail": str(exc) or "Forbidden", "code": "forbidden"}, status_code=403
        )
