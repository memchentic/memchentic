"""ASGI entrypoint: FastAPI REST + OAuth routes, FastMCP under ``/mcp``, optional SPA."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from alembic.config import Config
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from alembic import command
from memchentic.api.oauth import router as oauth_router
from memchentic.api.router import api_router
from memchentic.api.security_headers import SecurityHeadersMiddleware
from memchentic.auth.passwords import require_bcrypt_length
from memchentic.auth.principal import resolve_bearer
from memchentic.auth.tokens import verify_signing_key
from memchentic.db.session import get_session_factory, init_db, set_rls_context
from memchentic.events import start_memory_listener, stop_memory_listener
from memchentic.mcp.context import principal_var, session_var
from memchentic.mcp.server import create_mcp
from memchentic.services.errors import ServiceError, http_error
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
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                principal_var.reset(p_token)
                session_var.reset(s_token)


def _alembic_config() -> Config:
    """Build the Alembic config for a programmatic migration run.

    The database URL is not stored here. ``alembic/env.py`` reads it from
    ``get_settings().migration_database_url``. A ``ConfigParser`` value may
    not contain a bare ``%``, so a percent-encoded password would raise and
    the error message would print the whole URL.

    Returns:
        Config pointing at the bundled ``alembic.ini`` and script directory.
    """
    cfg = Config(str(API_ROOT / "alembic.ini"))
    # ConfigParser interpolation: a literal "%" in a value is written "%%".
    cfg.set_main_option("script_location", str(API_ROOT / "alembic").replace("%", "%%"))
    # env.py must not call logging.config.fileConfig in the server process.
    cfg.attributes["configure_logger"] = False
    return cfg


def _run_migrations() -> None:
    """Apply Alembic migrations to head (blocking; run off the event loop)."""
    command.upgrade(_alembic_config(), "head")


def create_app() -> FastAPI:
    """Assemble the FastAPI application with MCP, REST, OAuth, and SPA routes.

    Returns:
        Configured FastAPI app.
    """
    settings = get_settings()
    mcp_app = create_mcp().http_app(
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
        await init_db()
        await asyncio.to_thread(_run_migrations)
        logger.info("Migrations applied")
        async with get_session_factory()() as session:
            from memchentic.auth.users import ensure_admin_user
            from memchentic.services.auth_groups import ensure_auth_groups
            from memchentic.services.instance_settings import ensure_instance_settings
            from memchentic.services.memories import backfill_orphan_creators

            await set_rls_context(session, user_id=None, is_admin=True)
            await ensure_admin_user(session, settings)
            await ensure_auth_groups(session, settings)
            await ensure_instance_settings(session, settings)
            # Attribute orphan memories to the space owner only (never silently
            # claim them as the env admin — that hides deleted-owner history).
            # The helper binds its own admin RLS context: the calls above rebind
            # and commit, and every commit clears the context.
            backfilled = await backfill_orphan_creators(session)
            if backfilled:
                logger.info("Backfilled created_by on %s memories to space owner", backfilled)
            if settings.oauth_provider == "internal":
                # Refuse to start when ENCRYPTION_KEY cannot decrypt the stored
                # signing key; otherwise every sign-in fails at request time.
                await verify_signing_key(session, settings)
        from memchentic.embeddings import get_embedder

        # Fail closed before serving if MiniLM is missing (unless hash allowed).
        get_embedder()
        await start_memory_listener()
        try:
            async with mcp_app.lifespan(app):
                yield
        finally:
            await stop_memory_listener()

    docs_on = settings.enable_api_docs
    app = FastAPI(
        title="memchentic",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if docs_on else None,
        redoc_url="/redoc" if docs_on else None,
        openapi_url="/openapi.json" if docs_on else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["WWW-Authenticate", "Mcp-Session-Id"],
    )
    app.add_middleware(McpAuthMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.base_url.startswith("https://"))

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

    app.include_router(oauth_router)
    app.include_router(api_router)

    @app.get("/api/provider")
    async def provider_info() -> dict[str, object]:
        """Expose non-secret auth configuration for the GUI.

        Returns:
            Active provider name, public URLs, and whether web OAuth Sign-in
            is available (external IdP with ``OAUTH_CLIENT_ID`` set).
        """
        from memchentic.auth.providers import provider_label
        from memchentic.db.session import get_session_factory
        from memchentic.services.instance_settings import get_register_enabled

        live = get_settings()
        async with get_session_factory()() as session:
            register_enabled = await get_register_enabled(session)
        return {
            "oauth_provider": live.oauth_provider,
            "base_url": live.base_url,
            "mcp_url": live.mcp_resource_url,
            "web_oauth": live.web_oauth_enabled,
            "oauth_label": provider_label(live),
            "register_enabled": register_enabled,
        }

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
    """Run the development server with autoreload."""
    import uvicorn

    # Development entry point; inside the container the server must listen on all interfaces.
    uvicorn.run("memchentic.main:app", host="0.0.0.0", port=8080, reload=True)  # noqa: S104
