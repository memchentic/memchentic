"""Transport and browser-hardening response headers."""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

HSTS_VALUE = b"max-age=31536000; includeSubDomains"


class SecurityHeadersMiddleware:
    """Add baseline security headers to every HTTP response.

    Pure ASGI (not ``BaseHTTPMiddleware``) so SSE and MCP streaming bodies pass
    through unbuffered. Headers a route already set are left alone.
    """

    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        """Wrap ``app``.

        Args:
            app: Downstream ASGI app.
            hsts: Emit ``Strict-Transport-Security``; only valid when the public
                ``BASE_URL`` is https.
        """
        self.app = app
        self.defaults: list[tuple[bytes, bytes]] = [
            (b"x-content-type-options", b"nosniff"),
            (b"referrer-policy", b"no-referrer"),
            (b"x-frame-options", b"DENY"),
            (
                b"content-security-policy",
                b"default-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
            ),
            (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
        ]
        if hsts:
            self.defaults.append((b"strict-transport-security", HSTS_VALUE))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Inject defaults into ``http.response.start``."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers.extend(h for h in self.defaults if h[0] not in present)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
