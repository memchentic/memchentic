"""Startup secret checks report the real requirement for each secret."""

from __future__ import annotations

from memchentic.settings import Settings

STRONG_16 = "a-strong-value-0016"
STRONG_32 = "a-strong-value-of-thirty-two-chr"


def _settings(**overrides: str) -> Settings:
    values = {
        "ADMIN_PASSWORD": STRONG_16,
        "ADMIN_API_KEY": STRONG_16,
        "ENCRYPTION_KEY": STRONG_32,
        "POSTGRES_PASSWORD": STRONG_16,
        "POSTGRES_APP_PASSWORD": STRONG_16,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_strong_values_have_no_problems() -> None:
    settings = _settings()
    assert settings.insecure_secret_problems() == {}
    assert settings.insecure_secret_names() == []


def test_short_encryption_key_reports_32() -> None:
    problems = _settings(ENCRYPTION_KEY="x" * 20).insecure_secret_problems()
    assert list(problems) == ["ENCRYPTION_KEY"]
    assert "32+" in problems["ENCRYPTION_KEY"]
    assert "x" * 20 not in problems["ENCRYPTION_KEY"]


def test_placeholder_and_empty_are_named() -> None:
    settings = _settings(ADMIN_PASSWORD="change-me-please-now", POSTGRES_PASSWORD="")
    problems = settings.insecure_secret_problems()
    assert "placeholder" in problems["ADMIN_PASSWORD"]
    assert "empty" in problems["POSTGRES_PASSWORD"]
    assert settings.insecure_secret_names() == ["ADMIN_PASSWORD", "POSTGRES_PASSWORD"]
