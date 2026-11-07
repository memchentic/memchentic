from functools import lru_cache
from typing import Literal

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

    memchentic_base_url: str = Field(default="http://localhost:8080", alias="MEMCHENTIC_BASE_URL")
    oauth_provider: OAuthProvider = Field(default="internal", alias="OAUTH_PROVIDER")
    oauth_client_id: str = Field(default="", alias="OAUTH_CLIENT_ID")
    oauth_client_secret: str = Field(default="", alias="OAUTH_CLIENT_SECRET")
    oauth_issuer: str = Field(default="", alias="OAUTH_ISSUER")
    oauth_audience: str = Field(default="", alias="OAUTH_AUDIENCE")
    oauth_jwks_uri: str = Field(default="", alias="OAUTH_JWKS_URI")
    oauth_scopes: str = Field(default="openid,profile,email", alias="OAUTH_SCOPES")

    memchentic_admin_user: str = Field(default="admin", alias="MEMCHENTIC_ADMIN_USER")
    memchentic_admin_email: str = Field(default="", alias="MEMCHENTIC_ADMIN_EMAIL")
    memchentic_admin_password: str = Field(default="change-me", alias="MEMCHENTIC_ADMIN_PASSWORD")
    memchentic_admin_api_key: str = Field(
        default="change-me-admin-api-key", alias="MEMCHENTIC_ADMIN_API_KEY"
    )

    @property
    def admin_contact_email(self) -> str:
        """Email shown on the admin principal; falls back to the admin username."""
        email = self.memchentic_admin_email.strip()
        return email or self.memchentic_admin_user

    database_url: str = Field(
        default="postgresql+asyncpg://memchentic:memchentic@localhost:5432/memchentic",
        alias="DATABASE_URL",
    )

    embedding_model_path: str = Field(
        default="/opt/memchentic/models/all-MiniLM-L6-v2", alias="EMBEDDING_MODEL_PATH"
    )
    embedding_dim: int = Field(default=384, alias="EMBEDDING_DIM")
    near_dup_threshold: float = Field(default=0.92, alias="NEAR_DUP_THRESHOLD")
    rrf_k: int = Field(default=60, alias="RRF_K")

    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173", alias="CORS_ORIGINS"
    )
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    session_remember_days: int = Field(default=30, ge=1, alias="MEMCHENTIC_SESSION_REMEMBER_DAYS")
    session_browser_hours: int = Field(default=12, ge=1, alias="MEMCHENTIC_SESSION_BROWSER_HOURS")
    session_cookie_secure: bool | None = Field(default=None, alias="MEMCHENTIC_SESSION_COOKIE_SECURE")

    @property
    def cookie_secure(self) -> bool:
        """Whether the session cookie carries ``Secure``; defaults on for https base URLs."""
        if self.session_cookie_secure is not None:
            return self.session_cookie_secure
        return self.memchentic_base_url.startswith("https://")

    def insecure_secret_names(self) -> list[str]:
        """Name break-glass secrets that are empty, placeholders, or too short.

        Returns:
            Env var names that must be changed before the API may start.
        """
        weak = {"", "change-me", "change-me-admin-api-key"}
        checks = {
            "MEMCHENTIC_ADMIN_PASSWORD": self.memchentic_admin_password,
            "MEMCHENTIC_ADMIN_API_KEY": self.memchentic_admin_api_key,
        }
        return [name for name, value in checks.items() if value in weak or len(value) < 16]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def mcp_resource_url(self) -> str:
        return f"{self.memchentic_base_url.rstrip('/')}/mcp"

    @property
    def effective_issuer(self) -> str:
        if self.oauth_provider == "internal":
            return self.memchentic_base_url.rstrip("/")
        return self.oauth_issuer or self.memchentic_base_url.rstrip("/")

    @property
    def effective_audience(self) -> str:
        return self.oauth_audience or self.mcp_resource_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
