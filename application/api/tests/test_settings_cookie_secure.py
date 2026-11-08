"""Settings: blank SESSION_COOKIE_SECURE is treated as unset."""

from __future__ import annotations

import pytest

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
