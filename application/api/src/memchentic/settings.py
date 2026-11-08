from functools import lru_cache
from typing import Literal
from urllib.parse import quote_plus

from pydantic import Field
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
    postgres_hostname: str = Field(default="postgres", alias="POSTGRES_HOSTNAME")
    postgres_port: int = Field(default=5432, ge=1, le=65535, alias="POSTGRES_PORT")
    postgres_db: str = Field(default="memchentic", alias="POSTGRES_DB")

    @property
    def database_url(self) -> str:
        """SQLAlchemy async DSN built from ``POSTGRES_*`` pieces.

        Use ``POSTGRES_HOSTNAME`` for the Compose service name (``postgres``) or
        a remote host. No separate ``DATABASE_URL`` env var.
        """
        user = quote_plus(self.postgres_user)
        password = quote_plus(self.postgres_password)
        host = self.postgres_hostname.strip()
        db = quote_plus(self.postgres_db)
        return f"postgresql+asyncpg://{user}:{password}@{host}:{self.postgres_port}/{db}"

    near_dup_threshold: float = Field(default=0.92, alias="NEAR_DUP_THRESHOLD")
    rrf_k: int = Field(default=60, alias="RRF_K")

    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173", alias="CORS_ORIGINS"
    )
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    session_remember_days: int = Field(default=30, ge=1, alias="SESSION_REMEMBER_DAYS")
    session_browser_hours: int = Field(default=12, ge=1, alias="SESSION_BROWSER_HOURS")
    session_cookie_secure: bool | None = Field(default=None, alias="SESSION_COOKIE_SECURE")

    @property
    def cookie_secure(self) -> bool:
        """Whether the session cookie carries ``Secure``; defaults on for https base URLs."""
        if self.session_cookie_secure is not None:
            return self.session_cookie_secure
        return self.base_url.startswith("https://")

    def insecure_secret_names(self) -> list[str]:
        """Name break-glass secrets that are empty, placeholders, or too short.

        Returns:
            Env var names that must be changed before the API may start.
        """
        weak = {"", "change-me", "change-me-admin-api-key"}
        checks = {
            "ADMIN_PASSWORD": self.admin_password,
            "ADMIN_API_KEY": self.admin_api_key,
        }
        return [name for name, value in checks.items() if value in weak or len(value) < 16]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def mcp_resource_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/mcp"

    @property
    def effective_issuer(self) -> str:
        if self.oauth_provider == "internal":
            return self.base_url.rstrip("/")
        return self.oauth_issuer or self.base_url.rstrip("/")

    @property
    def effective_audience(self) -> str:
        return self.oauth_audience or self.mcp_resource_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
