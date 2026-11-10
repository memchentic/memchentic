"""Settings: session cookie Secure default and result-limit clamps."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from memchentic.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_blank_session_cookie_secure_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "")
    monkeypatch.setenv("BASE_URL", "http://example.test:8080")
    settings = Settings()
    assert settings.session_cookie_secure is None
    # Non-loopback hosts default Secure on (even over http BASE_URL).
    assert settings.cookie_secure is True


def test_loopback_http_defaults_cookie_insecure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("BASE_URL", "http://127.0.0.1:8080")
    settings = Settings()
    assert settings.session_cookie_secure is None
    assert settings.cookie_secure is False


def test_https_base_url_defaults_cookie_secure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SESSION_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("BASE_URL", "https://example.test")
    settings = Settings()
    assert settings.session_cookie_secure is None
    assert settings.cookie_secure is True


def test_result_limit_env_defaults_and_clamp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEARCH_DEFAULT_LIMIT", "5")
    monkeypatch.setenv("BROWSE_DEFAULT_LIMIT", "10")
    monkeypatch.setenv("RESULT_MAX_LIMIT", "8")
    settings = Settings()
    assert settings.resolve_search_limit(None) == 5
    assert settings.resolve_search_limit(3) == 3
    assert settings.resolve_search_limit(99) == 8
    assert settings.resolve_browse_limit(None) == 8  # browse default 10 clamped to max 8
    assert settings.resolve_browse_limit(2) == 2


_STRONG_SECRETS = {
    "ADMIN_PASSWORD": "strong-admin-password-01",
    "ADMIN_API_KEY": "strong-admin-api-key-01",
    "ENCRYPTION_KEY": "strong-encryption-key-0123456789ab",
    "POSTGRES_PASSWORD": "strong-owner-password-01",
    "POSTGRES_APP_PASSWORD": "strong-app-password-01",
}


def test_insecure_secret_names_accepts_strong_values() -> None:
    assert Settings(**_STRONG_SECRETS).insecure_secret_names() == []


@pytest.mark.parametrize("name", sorted(_STRONG_SECRETS))
@pytest.mark.parametrize(
    "weak", ["", "change-me", "short", "prefix-change-me-suffix-long-enough-0123"]
)
def test_insecure_secret_names_flags_weak_value(name: str, weak: str) -> None:
    """Empty, placeholder, too-short and placeholder-containing values are all named."""
    settings = Settings(**{**_STRONG_SECRETS, name: weak})
    assert settings.insecure_secret_names() == [name]


def test_app_refuses_to_start_with_placeholder_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """The lifespan raises before it touches the database."""
    monkeypatch.setenv("ADMIN_PASSWORD", "change-me")
    get_settings.cache_clear()
    from memchentic.main import create_app

    app = create_app()
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"), TestClient(app):
        pass
