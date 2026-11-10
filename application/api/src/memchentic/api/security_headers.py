"""Transport and browser-hardening response headers."""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

HSTS_VALUE = b"max-age=31536000; includeSubDomains"

DEFAULT_CSP = b"default-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"

# FastAPI's Swagger UI and ReDoc pages load their script and stylesheet from a
# CDN and start with an inline script. This policy is sent for the docs paths
# only, and only when ENABLE_API_DOCS is on.
DOCS_CSP = (
    b"default-src 'self'; "
    b"script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    b"style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
    b"font-src 'self' https://fonts.gstatic.com; "
    b"img-src 'self' data: https://fastapi.tiangolo.com; "
    b"worker-src 'self' blob:; "
    b"frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


def _headers(csp: bytes, *, hsts: bool) -> list[tuple[bytes, bytes]]:
    """Build the default header list for one content security policy."""
    headers: list[tuple[bytes, bytes]] = [
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"no-referrer"),
        (b"x-frame-options", b"DENY"),
        (b"content-security-policy", csp),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    ]
    if hsts:
        headers.append((b"strict-transport-security", HSTS_VALUE))
    return headers


class SecurityHeadersMiddleware:
    """Add baseline security headers to every HTTP response.

    Pure ASGI (not ``BaseHTTPMiddleware``) so SSE and MCP streaming bodies pass
    through unbuffered. Headers a route already set are left alone.
    """

    def __init__(self, app: ASGIApp, *, hsts: bool, docs_paths: tuple[str, ...] = ()) -> None:
        """Wrap ``app``.

        Args:
            app: Downstream ASGI app.
            hsts: Emit ``Strict-Transport-Security``; only valid when the public
                ``BASE_URL`` is https.
            docs_paths: Path prefixes of the API docs pages (for example
                ``("/docs", "/redoc")``). Responses for these paths get
                ``DOCS_CSP`` instead of ``DEFAULT_CSP``. Empty when the docs
                pages are disabled.
        """
        self.app = app
        self.docs_paths = docs_paths
        self.defaults = _headers(DEFAULT_CSP, hsts=hsts)
        self.docs_defaults = _headers(DOCS_CSP, hsts=hsts)

    def _is_docs_path(self, path: str) -> bool:
        """Report whether ``path`` is a docs page or a sub-path of one."""
        return any(path == prefix or path.startswith(prefix + "/") for prefix in self.docs_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Inject defaults into ``http.response.start``."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        defaults = self.docs_defaults if self._is_docs_path(scope["path"]) else self.defaults

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name.lower() for name, _ in headers}
                headers.extend(h for h in defaults if h[0] not in present)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
