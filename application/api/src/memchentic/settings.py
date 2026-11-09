import ssl
from functools import lru_cache
from typing import Literal
from urllib.parse import quote_plus

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

OAuthProvider = Literal[
    "google",
    "github",
    "okta",
    "authentik",
    "keycloak",
    "auth0",
    "entra",
    "gitlab",
    "generic",
    "internal",
]

PostgresSslMode = Literal["disable", "prefer", "require", "verify-ca", "verify-full"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    base_url: str = Field(default="http://localhost:8080", alias="BASE_URL")
    oauth_provider: OAuthProvider = Field(default="internal", alias="OAUTH_PROVIDER")
    oauth_client_id: str = Field(default="", alias="OAUTH_CLIENT_ID")
    oauth_client_secret: str = Field(default="", alias="OAUTH_CLIENT_SECRET")
    oauth_issuer: str = Field(default="", alias="OAUTH_ISSUER")
    oauth_audience: str = Field(default="", alias="OAUTH_AUDIENCE")
    oauth_jwks_uri: str = Field(default="", alias="OAUTH_JWKS_URI")
    oauth_scopes: str = Field(default="openid,profile,email", alias="OAUTH_SCOPES")

    admin_display_name: str = Field(default="Admin", alias="ADMIN_DISPLAY_NAME")
    admin_email: str = Field(default="admin@example.com", alias="ADMIN_EMAIL")
    admin_password: str = Field(default="change-me", alias="ADMIN_PASSWORD")
    admin_api_key: str = Field(default="change-me-admin-api-key", alias="ADMIN_API_KEY")

    @property
    def admin_contact_email(self) -> str:
        """Canonical email on the break-glass admin principal (login identity).

        ``ADMIN_EMAIL`` is required in real deployments. Empty values fall back
        to ``admin@example.com`` so local placeholders still boot.
        """
        email = self.admin_email.strip()
        return email or "admin@example.com"

    postgres_user: str = Field(default="memchentic", alias="POSTGRES_USER")
    postgres_password: str = Field(default="memchentic", alias="POSTGRES_PASSWORD")
    # Runtime role (NOSUPERUSER, NOBYPASSRLS). Migrations use POSTGRES_USER.
    postgres_app_user: str = Field(default="memchentic_app", alias="POSTGRES_APP_USER")
    postgres_app_password: str = Field(default="", alias="POSTGRES_APP_PASSWORD")
    postgres_hostname: str = Field(default="postgres", alias="POSTGRES_HOSTNAME")
    postgres_port: int = Field(default=5432, ge=1, le=65535, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="memchentic", alias="POSTGRES_DB")
    postgres_sslmode: PostgresSslMode = Field(default="prefer", alias="POSTGRES_SSLMODE")
    postgres_sslrootcert: str = Field(default="", alias="POSTGRES_SSLROOTCERT")

    # Encrypts secrets the app itself stores in Postgres (internal JWT signing keys).
    # Changing it makes existing signing keys unreadable; rotate by re-keying the rows.
    encryption_key: str = Field(default="", alias="ENCRYPTION_KEY")

    @property
    def postgres_ssl(self) -> str | ssl.SSLContext:
        """asyncpg ``ssl`` argument for ``POSTGRES_SSLMODE``.

        ``verify-ca`` / ``verify-full`` build a context trusting
        ``POSTGRES_SSLROOTCERT`` (system CAs when empty); ``verify-full`` also
        checks the server hostname. Other modes pass through as libpq names.
        """
        mode = self.postgres_sslmode
        if mode not in ("verify-ca", "verify-full"):
            return mode
        ctx = ssl.create_default_context(cafile=self.postgres_sslrootcert.strip() or None)
        ctx.check_hostname = mode == "verify-full"
        return ctx

    def _dsn(self, *, user: str, password: str) -> str:
        host = self.postgres_hostname.strip()
        db = quote_plus(self.postgres_db)
        return (
            f"postgresql+asyncpg://{quote_plus(user)}:{quote_plus(password)}"
            f"@{host}:{self.postgres_port}/{db}"
        )

    @property
    def migration_database_url(self) -> str:
        """Owner/superuser DSN for Alembic (table owner; can CREATE ROLE)."""
        return self._dsn(user=self.postgres_user, password=self.postgres_password)

    @property
    def database_url(self) -> str:
        """Runtime DSN as the non-superuser app role (RLS enforced).

        Falls back to the owner URL only when ``POSTGRES_APP_PASSWORD`` is empty
        (legacy local) — production must set the app password.
        """
        app_user = self.postgres_app_user.strip() or "memchentic_app"
        app_password = self.postgres_app_password
        if not app_password:
            return self.migration_database_url
        return self._dsn(user=app_user, password=app_password)
    near_dup_threshold: float = Field(default=0.92, alias="NEAR_DUP_THRESHOLD")
    rrf_k: int = Field(default=60, alias="RRF_K")
    near_dup_candidates: int = Field(default=5, ge=1, alias="NEAR_DUP_CANDIDATES")
    overlap_scan_limit: int = Field(default=2000, ge=1, alias="OVERLAP_SCAN_LIMIT")
    search_recency_half_life_days: float = Field(
        default=30.0, gt=0, alias="SEARCH_RECENCY_HALF_LIFE_DAYS"
    )
    search_path_boost: float = Field(default=0.15, ge=0, alias="SEARCH_PATH_BOOST")

    # Sliding TTL: 0 disables the default / ceiling respectively.
    memory_default_ttl_days: int = Field(default=90, ge=0, alias="MEMORY_DEFAULT_TTL_DAYS")
    memory_ttl_extend_days: int = Field(default=90, ge=0, alias="MEMORY_TTL_EXTEND_DAYS")
    memory_ttl_touch_debounce_hours: float = Field(
        default=6.0, ge=0, alias="MEMORY_TTL_TOUCH_DEBOUNCE_HOURS"
    )
    memory_max_ttl_days: int = Field(default=730, ge=0, alias="MEMORY_MAX_TTL_DAYS")
    memory_usage_score_weight: float = Field(
        default=0.05, ge=0, alias="MEMORY_USAGE_SCORE_WEIGHT"
    )

    # Default result counts when callers omit ``limit`` (MCP/REST/services).
    search_default_limit: int = Field(default=20, ge=1, alias="SEARCH_DEFAULT_LIMIT")
    browse_default_limit: int = Field(default=50, ge=1, alias="BROWSE_DEFAULT_LIMIT")
    result_max_limit: int = Field(default=100, ge=1, alias="RESULT_MAX_LIMIT")

    def resolve_search_limit(self, limit: int | None) -> int:
        """Clamp a search/find limit, substituting the env default when omitted."""
        raw = self.search_default_limit if limit is None else limit
        return max(1, min(int(raw), self.result_max_limit))

    def resolve_browse_limit(self, limit: int | None) -> int:
        """Clamp a browse/list limit, substituting the env default when omitted."""
        raw = self.browse_default_limit if limit is None else limit
        return max(1, min(int(raw), self.result_max_limit))

    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173", alias="CORS_ORIGINS"
    )
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    session_remember_days: int = Field(default=30, ge=1, alias="SESSION_REMEMBER_DAYS")
    session_browser_hours: int = Field(default=12, ge=1, alias="SESSION_BROWSER_HOURS")
    session_cookie_secure: bool | None = Field(default=None, alias="SESSION_COOKIE_SECURE")
    register_enabled: bool = Field(default=True, alias="REGISTER_ENABLED")
    enable_api_docs: bool = Field(default=False, alias="ENABLE_API_DOCS")
    allow_hash_embeddings: bool = Field(
        default=False, alias="ALLOW_HASH_EMBEDDINGS"
    )

    @field_validator("session_cookie_secure", mode="before")
    @classmethod
    def _empty_optional_bool(cls, value: object) -> object:
        """Treat blank env values as unset (Compose often ships ``KEY=``)."""
        if value is None or value == "":
            return None
        return value

    @property
    def cookie_secure(self) -> bool:
        """Whether the session cookie carries ``Secure``.

        Explicit ``SESSION_COOKIE_SECURE`` wins. Otherwise Secure is on unless
        the ``BASE_URL`` host is loopback (local http dev).
        """
        if self.session_cookie_secure is not None:
            return self.session_cookie_secure
        from urllib.parse import urlparse

        host = (urlparse(self.base_url).hostname or "").casefold()
        return host not in {"localhost", "127.0.0.1", "::1"}
    def insecure_secret_names(self) -> list[str]:
        """Name break-glass secrets that are empty, placeholders, or too short.

        Returns:
            Env var names that must be changed before the API may start.
        """
        weak_exact = {"", "change-me", "change-me-admin-api-key", "memchentic"}
        checks: dict[str, tuple[str, int]] = {
            "ADMIN_PASSWORD": (self.admin_password, 16),
            "ADMIN_API_KEY": (self.admin_api_key, 16),
            "ENCRYPTION_KEY": (self.encryption_key, 32),
            "POSTGRES_PASSWORD": (self.postgres_password, 16),
            "POSTGRES_APP_PASSWORD": (self.postgres_app_password, 16),
        }
        bad: list[str] = []
        for name, (value, min_len) in checks.items():
            lowered = value.casefold()
            if (
                value in weak_exact
                or "change-me" in lowered
                or len(value) < min_len
            ):
                bad.append(name)
        return bad
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def mcp_resource_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/mcp"

    @property
    def session_issuer(self) -> str:
        """Issuer claim for memchentic-signed GUI/session access tokens.

        Always this instance's ``BASE_URL``, even when ``OAUTH_PROVIDER`` is an
        external IdP (IdP JWTs keep their own issuer via JWKS verification).
        """
        return self.base_url.rstrip("/")

    @property
    def effective_audience(self) -> str:
        return self.oauth_audience or self.mcp_resource_url

    @property
    def web_oauth_enabled(self) -> bool:
        """True when an external IdP client is configured for browser Sign-in."""
        return self.oauth_provider != "internal" and bool(self.oauth_client_id.strip())

    def web_oauth_redirect_uri(self) -> str:
        """Fixed browser OAuth callback registered on the IdP."""
        return f"{self.base_url.rstrip('/')}/api/auth/oauth/callback"


@lru_cache
def get_settings() -> Settings:
    return Settings()
