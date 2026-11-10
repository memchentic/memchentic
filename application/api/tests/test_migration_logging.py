"""Migrations must not disable or reconfigure the loggers of the process."""

from __future__ import annotations

import logging

from alembic.config import Config

from alembic import command


def test_boot_migrations_keep_process_logging() -> None:
    """The programmatic run used at boot leaves logging exactly as it was."""
    from memchentic.db.migrate import _alembic_config

    probe = logging.getLogger("memchentic.migration_logging_probe")
    uvicorn_error = logging.getLogger("uvicorn.error")
    probe.disabled = False
    uvicorn_error.disabled = False
    root = logging.getLogger()
    level_before = root.level
    handlers_before = list(root.handlers)

    command.upgrade(_alembic_config(), "head")

    assert probe.disabled is False
    assert uvicorn_error.disabled is False
    assert root.level == level_before
    assert list(root.handlers) == handlers_before


def test_command_line_style_run_keeps_existing_loggers_enabled() -> None:
    """Without the attribute (Alembic CLI) alembic.ini applies, but disables nothing."""
    from memchentic.main import API_ROOT

    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic").replace("%", "%%"))
    probe = logging.getLogger("memchentic.migration_logging_probe_cli")
    probe.disabled = False
    root = logging.getLogger()
    level_before = root.level
    handlers_before = list(root.handlers)
    try:
        command.upgrade(cfg, "head")
        assert probe.disabled is False
    finally:
        # alembic.ini replaces the root handlers and level; give pytest its own back.
        root.setLevel(level_before)
        root.handlers[:] = handlers_before
