"""Alembic migration runner shared by application startup and the test suite."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config

from alembic import command

_API_ROOT = Path(__file__).resolve().parents[3]


def _alembic_config() -> Config:
    """Build the Alembic config for a programmatic migration run.

    The database URL is not stored here. ``alembic/env.py`` reads it from
    ``get_settings().migration_database_url``. A ``ConfigParser`` value may
    not contain a bare ``%``, so a percent-encoded password would raise and
    the error message would print the whole URL.

    Returns:
        Config pointing at the bundled ``alembic.ini`` and script directory.
    """
    cfg = Config(str(_API_ROOT / "alembic.ini"))
    # ConfigParser interpolation: a literal "%" in a value is written "%%".
    cfg.set_main_option("script_location", str(_API_ROOT / "alembic").replace("%", "%%"))
    # env.py must not call logging.config.fileConfig in the server process.
    cfg.attributes["configure_logger"] = False
    return cfg


def run_migrations() -> None:
    """Apply Alembic migrations to head (blocking; run off the event loop)."""
    command.upgrade(_alembic_config(), "head")
