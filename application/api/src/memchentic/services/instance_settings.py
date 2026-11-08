"""Instance-wide System Settings (DB-backed, seeded from env)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.models.entities import InstanceSetting
from memchentic.settings import Settings, get_settings

if TYPE_CHECKING:
    from memchentic.auth.principal import Principal

KEY_REGISTER_ENABLED = "register_enabled"


def _require_admin(principal: Principal) -> None:
    if not getattr(principal, "is_admin", False):
        raise PermissionError("Administrators only")


def _parse_bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


async def ensure_instance_settings(
    session: AsyncSession, settings: Settings | None = None
) -> None:
    """Sync ``REGISTER_ENABLED`` from env into the DB on every boot.

    Compose/Helm env is the deploy source of truth. Admins can still flip the
    value live in System Settings until the next process start reseeds it.

    Args:
        session: Database session.
        settings: Optional settings override.
    """
    settings = settings or get_settings()
    value = "true" if settings.register_enabled else "false"
    result = await session.execute(
        select(InstanceSetting).where(InstanceSetting.key == KEY_REGISTER_ENABLED)
    )
    row = result.scalar_one_or_none()
    if row is None:
        session.add(InstanceSetting(key=KEY_REGISTER_ENABLED, value=value))
    else:
        row.value = value
    await session.commit()


async def get_register_enabled(session: AsyncSession) -> bool:
    """Return whether public self-registration is allowed.

    Falls back to ``REGISTER_ENABLED`` when the DB row is not seeded yet.
    """
    result = await session.execute(
        select(InstanceSetting).where(InstanceSetting.key == KEY_REGISTER_ENABLED)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return get_settings().register_enabled
    return _parse_bool(row.value)


async def set_register_enabled(
    session: AsyncSession, principal: Principal, enabled: bool
) -> bool:
    """Admin-only update for public self-registration.

    Args:
        session: Database session.
        principal: Caller (must be administrator).
        enabled: New value.

    Returns:
        The stored value.

    Raises:
        PermissionError: When the caller is not an administrator.
    """
    _require_admin(principal)
    result = await session.execute(
        select(InstanceSetting).where(InstanceSetting.key == KEY_REGISTER_ENABLED)
    )
    row = result.scalar_one_or_none()
    value = "true" if enabled else "false"
    if row is None:
        session.add(InstanceSetting(key=KEY_REGISTER_ENABLED, value=value))
    else:
        row.value = value
    await session.commit()
    return enabled
