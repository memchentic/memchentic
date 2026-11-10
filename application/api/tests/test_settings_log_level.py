"""Settings: LOG_LEVEL accepts any letter case and rejects unknown names."""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from memchentic.settings import Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("debug", "DEBUG"), (" Warning ", "WARNING"), ("INFO", "INFO"), ("", "INFO")],
)
def test_log_level_is_normalized(monkeypatch: pytest.MonkeyPatch, raw: str, expected: str) -> None:
    monkeypatch.setenv("LOG_LEVEL", raw)
    settings = Settings()
    assert settings.log_level == expected
    # The value is one the standard library accepts (what ``lifespan`` passes on).
    assert expected in logging.getLevelNamesMapping()


def test_unknown_log_level_is_rejected_at_load(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "verbose")
    with pytest.raises(ValidationError, match="LOG_LEVEL must be one of"):
        Settings()
