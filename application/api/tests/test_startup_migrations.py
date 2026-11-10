"""Boot migrations: the database URL never enters the Alembic config."""

from __future__ import annotations

import inspect


def test_boot_migration_config_does_not_hold_the_dsn() -> None:
    from memchentic.db.migrate import _alembic_config, run_migrations

    cfg = _alembic_config()

    # The value is still the placeholder from alembic.ini.
    assert cfg.get_main_option("sqlalchemy.url") == "driver://user:pass@localhost/dbname"
    assert list(inspect.signature(run_migrations).parameters) == []
