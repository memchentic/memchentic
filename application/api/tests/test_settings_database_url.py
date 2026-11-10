"""Settings.database_url never falls back to the owner role."""

from __future__ import annotations

import pytest

from memchentic.settings import Settings


def test_database_url_refuses_owner_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_APP_PASSWORD", "")
    settings = Settings()
    with pytest.raises(RuntimeError, match="POSTGRES_APP_PASSWORD"):
        _ = settings.database_url


def test_database_url_uses_app_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_USER", "owner_role")
    monkeypatch.setenv("POSTGRES_APP_USER", "runtime_role")
    monkeypatch.setenv("POSTGRES_APP_PASSWORD", "runtime-role-password-01")
    settings = Settings()
    assert "://runtime_role:" in settings.database_url
    assert "owner_role" not in settings.database_url
