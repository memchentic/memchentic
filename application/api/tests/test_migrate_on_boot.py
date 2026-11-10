"""RUN_MIGRATIONS_ON_BOOT: the application can start without the owner password."""

from __future__ import annotations

import pytest

from memchentic.settings import Settings


def test_owner_password_not_required_when_boot_migrations_are_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUN_MIGRATIONS_ON_BOOT", "false")
    monkeypatch.setenv("POSTGRES_PASSWORD", "")
    settings = Settings()
    assert settings.run_migrations_on_boot is False
    assert "POSTGRES_PASSWORD" not in settings.insecure_secret_names()
    assert "POSTGRES_PASSWORD" in settings.insecure_secret_names(for_migration=True)


def test_owner_password_required_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUN_MIGRATIONS_ON_BOOT", raising=False)
    monkeypatch.setenv("POSTGRES_PASSWORD", "")
    settings = Settings()
    assert settings.run_migrations_on_boot is True
    assert "POSTGRES_PASSWORD" in settings.insecure_secret_names()


async def test_migrate_entrypoint_is_idempotent() -> None:
    from memchentic.migrate import migrate

    await migrate()
