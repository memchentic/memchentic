"""ASGI entrypoint: FastAPI REST + OAuth routes, FastMCP under ``/mcp``, optional SPA."""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from types import FrameType
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from memchentic import __version__
from memchentic.api.errors import register_exception_handlers
from memchentic.api.oauth import router as oauth_router
from memchentic.api.router import api_router
from memchentic.api.security_headers import SecurityHeadersMiddleware
from memchentic.auth.bearer import resolve_bearer
from memchentic.auth.passwords import require_bcrypt_length
from memchentic.db.migrate import run_migrations
from memchentic.db.session import get_session_factory, init_db, set_rls_context
from memchentic.embeddings import get_embedder
from memchentic.events import close_memory_streams, start_memory_listener, stop_memory_listener
from memchentic.mcp.context import principal_var, session_var
from memchentic.mcp.server import mcp as mcp_server
from memchentic.services.bootstrap import bootstrap_instance
from memchentic.settings import Settings, get_settings

logger = logging.getLogger("memchentic")

API_ROOT = Path(__file__).resolve().parents[2]


def _bearer_from_scope(scope: Scope) -> str | None:
    """Extract a Bearer token from raw ASGI headers.

    Args:
        scope: ASGI HTTP scope.

    Returns:
        The token string, or ``None`` when no Bearer authorization header is present.
    """
    for key, value in scope.get("headers", []):
        if key.decode().lower() == "authorization":
            raw = value.decode()
            if raw.lower().startswith("bearer "):
                return raw[7:].strip() or None
    return None


def _www_authenticate(settings: Settings) -> bytes:
    """Build the RFC 9728 ``WWW-Authenticate`` challenge for MCP clients.

    Args:
        settings: Active settings (supplies the public base URL).

    Returns:
        Encoded header value pointing at protected resource metadata.
    """
    meta = f"{settings.base_url.rstrip('/')}/.well-known/oauth-protected-resource"
    return f'Bearer resource_metadata="{meta}", error="invalid_token"'.encode()


def _close_streams_on_signal(loop: asyncio.AbstractEventLoop) -> Callable[[], None]:
    """Chain SIGINT/SIGTERM so open SSE streams end when shutdown starts.

    uvicorn waits for in-flight responses before it runs lifespan shutdown,
    and an SSE response never finishes by itself. The chained handler asks
    every stream to end, then calls the handler that was installed before
    (uvicorn's own).

    Args:
        loop: The running event loop that owns the subscriber queues.

    Returns:
        A function that restores the previous handlers.
    """
    if threading.current_thread() is not threading.main_thread():
        # Signal handlers can only be set from the main thread. The test
        # client runs the lifespan in a worker thread; nothing to chain there.
        return lambda: None

    installed: dict[int, tuple[Any, Any]] = {}

    def _chain(previous: Any) -> Callable[[int, FrameType | None], None]:
        def _handler(signum: int, frame: FrameType | None) -> None:
            with suppress(RuntimeError):  # loop already closed
                loop.call_soon_threadsafe(close_memory_streams)
            previous(signum, frame)

        return _handler

    for sig in (signal.SIGINT, signal.SIGTERM):
        previous = signal.getsignal(sig)
        if not callable(previous):
            # SIG_DFL / SIG_IGN / not set from Python: keep the default action.
            continue
        handler = _chain(previous)
        signal.signal(sig, handler)
        installed[sig] = (handler, previous)

    def _restore() -> None:
        for sig, (handler, previous) in installed.items():
            if signal.getsignal(sig) is handler:
                signal.signal(sig, previous)

    return _restore


class McpAuthMiddleware:
    """Authenticate ``/mcp`` traffic and bind principal + DB session for tool handlers.

    Attributes:
        app: Downstream ASGI application.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Wrap a downstream ASGI app.

        Args:
            app: Downstream ASGI application.
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Gate MCP requests on a valid principal; pass other traffic through.

        The middleware never commits. Every service function commits its own
        work with ``commit_with_rls``. Whatever is still pending when the MCP
        app returns was left behind by a tool call that failed, and is rolled
        back. This is the same contract as the REST ``db_session`` dependency.

        Args:
            scope: ASGI scope.
            receive: ASGI receive callable.
            send: ASGI send callable.
        """
        path = scope.get("path") or ""
        if scope["type"] != "http" or not (path == "/mcp" or path.startswith("/mcp/")):
            await self.app(scope, receive, send)
            return
        if path == "/mcp":
            scope = {**scope, "path": "/mcp/", "raw_path": b"/mcp/"}
        if scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return

        settings = get_settings()
        async with get_session_factory()() as session:
            principal = await resolve_bearer(session, _bearer_from_scope(scope), settings)
            if principal is None:
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"www-authenticate", _www_authenticate(settings)),
                        ],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": json.dumps({"error": "unauthorized"}).encode(),
                    }
                )
                return

            await set_rls_context(session, user_id=principal.user_id, is_admin=principal.is_admin)
            p_token = principal_var.set(principal)
            s_token = session_var.set(session)
            try:
                await self.app(scope, receive, send)
            finally:
                principal_var.reset(p_token)
                session_var.reset(s_token)
                # Services commit their own work. FastMCP turns a tool exception
                # into an ``isError`` result without raising, so a commit here
                # would persist the half-applied changes of a failed tool call.
                await session.rollback()


def create_app() -> FastAPI:
    """Assemble the FastAPI application with MCP, REST, OAuth, and SPA routes.

    Returns:
        Configured FastAPI app.
    """
    settings = get_settings()
    mcp_app = mcp_server.http_app(
        path="/", transport="streamable-http", stateless_http=True, json_response=True
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Run startup (extensions, migrations, signing key) and FastMCP lifespan.

        Args:
            app: The FastAPI app being started.

        Yields:
            Control while the app serves requests.
        """
        logging.basicConfig(level=settings.log_level)
        problems = settings.insecure_secret_problems()
        if problems:
            details = "; ".join(f"{name} {reason}" for name, reason in problems.items())
            raise RuntimeError(f"Refusing to start: {details}")
        require_bcrypt_length(settings.admin_password, "ADMIN_PASSWORD")
        if settings.run_migrations_on_boot:
            await init_db()
            await asyncio.to_thread(run_migrations)
            logger.info("Migrations applied")
        else:
            logger.info("RUN_MIGRATIONS_ON_BOOT=false: extensions and migrations are skipped")
        async with get_session_factory()() as session:
            await bootstrap_instance(session, settings)
        # Fail closed before serving if MiniLM is missing (unless hash allowed).
        get_embedder()
        await start_memory_listener()
        restore_signals = _close_streams_on_signal(asyncio.get_running_loop())
        try:
            async with mcp_app.lifespan(app):
                yield
        finally:
            restore_signals()
            await stop_memory_listener()

    docs_on = settings.enable_api_docs
    app = FastAPI(
        title="memchentic",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if docs_on else None,
        redoc_url="/redoc" if docs_on else None,
        openapi_url="/openapi.json" if docs_on else None,
    )
    # Starlette runs the middleware added last first. CORSMiddleware must wrap
    # McpAuthMiddleware so the 401 written for /mcp carries the CORS headers;
    # SecurityHeadersMiddleware stays outermost.
    app.add_middleware(McpAuthMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["WWW-Authenticate", "Mcp-Session-Id"],
    )
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts=settings.base_url.startswith("https://"),
        docs_paths=("/docs", "/redoc") if docs_on else (),
    )

    register_exception_handlers(app)

    app.include_router(oauth_router)
    app.include_router(api_router)

    app.mount("/mcp", mcp_app)

    dist = API_ROOT / "gui" / "dist"
    index = dist / "index.html"
    if index.is_file():
        if (dist / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str) -> Response:
            """Serve the built SPA, falling back to ``index.html`` for client routes.

            Args:
                full_path: Requested path relative to the site root.

            Returns:
                A static file, the SPA index, or 404 for reserved prefixes.
            """
            if full_path.startswith(("api/", "mcp", "oauth/", ".well-known/")):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            try:
                candidate = (dist / full_path).resolve()
            except ValueError:
                # A NUL character in the path cannot name a file.
                return JSONResponse({"detail": "Not found"}, status_code=404)
            if full_path and candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(index)

    return app


app = create_app()


def run() -> None:
    """Run a development server with autoreload (the ``memchentic`` console script).

    Development only: it binds every interface and enables reload. The image and
    the Compose overlay start ``uvicorn memchentic.main:app`` and never call this.
    """
    import uvicorn

    # Development entry point; inside the container the server must listen on all interfaces.
    uvicorn.run(
        "memchentic.main:app",
        host="0.0.0.0",  # noqa: S104
        port=8080,
        reload=True,
        timeout_graceful_shutdown=5,
    )
