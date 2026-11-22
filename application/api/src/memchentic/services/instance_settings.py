"""Instance-wide System Settings (DB-backed, seeded from env)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.crypto import decrypt_secret, encrypt_secret, is_encrypted_secret
from memchentic.models.entities import InstanceSetting
from memchentic.services.errors import ForbiddenError
from memchentic.settings import Settings, get_settings

if TYPE_CHECKING:
    from memchentic.auth.principal import Principal

KEY_REGISTER_ENABLED = "register_enabled"
KEY_SMTP_HOST = "smtp_host"
KEY_SMTP_PORT = "smtp_port"
KEY_SMTP_USERNAME = "smtp_username"
KEY_SMTP_PASSWORD = "smtp_password"  # noqa: S105
KEY_SMTP_FROM = "smtp_from"
KEY_SMTP_USE_TLS = "smtp_use_tls"
KEY_AUTOMATION_RESOLVE_SUPERSEDES = "automation_resolve_supersedes"
KEY_AUTOMATION_RESOLVE_NEAR_DUPS = "automation_resolve_near_dups"
KEY_AUTOMATION_PROPOSE_CONSOLIDATIONS = "automation_propose_consolidations"
KEY_AUTOMATION_AUTO_APPLY_CONSOLIDATIONS = "automation_auto_apply_consolidations"
KEY_WEBHOOK_ENABLED = "webhook_enabled"
KEY_WEBHOOK_URL = "webhook_url"
KEY_WEBHOOK_SECRET = "webhook_secret"  # noqa: S105
KEY_WEBHOOK_FAIL_COUNT = "webhook_fail_count"

_SMTP_KEYS = (
    KEY_SMTP_HOST,
    KEY_SMTP_PORT,
    KEY_SMTP_USERNAME,
    KEY_SMTP_PASSWORD,
    KEY_SMTP_FROM,
    KEY_SMTP_USE_TLS,
)


def _require_admin(principal: Principal) -> None:
    if not getattr(principal, "is_admin", False):
        raise ForbiddenError("Administrators only")


def _parse_bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


async def _get_raw(session: AsyncSession, key: str) -> str | None:
    result = await session.execute(select(InstanceSetting).where(InstanceSetting.key == key))
    row = result.scalar_one_or_none()
    return None if row is None else row.value


async def _upsert(session: AsyncSession, key: str, value: str) -> None:
    result = await session.execute(select(InstanceSetting).where(InstanceSetting.key == key))
    row = result.scalar_one_or_none()
    if row is None:
        session.add(InstanceSetting(key=key, value=value))
    else:
        row.value = value


@dataclass(frozen=True, slots=True)
class MailSettings:
    """Resolved outbound-mail configuration from System Settings (or env fallback)."""

    host: str
    port: int
    username: str
    password: str
    from_address: str
    use_tls: bool

    @property
    def configured(self) -> bool:
        """True when host and From are both non-empty (enough to attempt SMTP)."""
        return bool(self.host.strip()) and bool(self.from_address.strip())

    @property
    def password_set(self) -> bool:
        return bool(self.password)


async def ensure_instance_settings(session: AsyncSession, settings: Settings | None = None) -> None:
    """Seed missing System Settings rows from env (existing rows are kept).

    ``REGISTER_ENABLED`` and the ``SMTP_*`` variables are only the values for
    the first boot of a new database. Once a row exists, the value an
    administrator chose in System Settings is the source of truth and is kept
    across restarts.

    Args:
        session: Database session.
        settings: Optional settings override.
    """
    settings = settings or get_settings()
    seeds: list[tuple[str, str]] = [
        (KEY_REGISTER_ENABLED, "true" if settings.register_enabled else "false"),
        (KEY_SMTP_HOST, settings.smtp_host.strip()),
        (KEY_SMTP_PORT, str(settings.smtp_port)),
        (KEY_SMTP_USERNAME, settings.smtp_username.strip()),
        (KEY_SMTP_FROM, settings.smtp_from.strip()),
        (KEY_SMTP_USE_TLS, "true" if settings.smtp_use_tls else "false"),
        (KEY_AUTOMATION_RESOLVE_SUPERSEDES, "false"),
        (KEY_AUTOMATION_RESOLVE_NEAR_DUPS, "false"),
        (KEY_AUTOMATION_PROPOSE_CONSOLIDATIONS, "false"),
        (KEY_AUTOMATION_AUTO_APPLY_CONSOLIDATIONS, "false"),
        (KEY_WEBHOOK_ENABLED, "false"),
        (KEY_WEBHOOK_URL, ""),
        (KEY_WEBHOOK_SECRET, ""),
        (KEY_WEBHOOK_FAIL_COUNT, "0"),
    ]
    password = settings.smtp_password
    seeds.append(
        (
            KEY_SMTP_PASSWORD,
            encrypt_secret(password, settings) if password else "",
        )
    )
    for key, value in seeds:
        await session.execute(
            pg_insert(InstanceSetting)
            .values(key=key, value=value)
            .on_conflict_do_nothing(index_elements=["key"])
        )
    await session.commit()


async def get_register_enabled(session: AsyncSession) -> bool:
    """Return whether public self-registration is allowed.

    Falls back to ``REGISTER_ENABLED`` when the DB row is not seeded yet.
    """
    raw = await _get_raw(session, KEY_REGISTER_ENABLED)
    if raw is None:
        return get_settings().register_enabled
    return _parse_bool(raw)


async def set_register_enabled(session: AsyncSession, principal: Principal, enabled: bool) -> bool:
    """Admin-only update for public self-registration.

    Args:
        session: Database session.
        principal: Caller (must be administrator).
        enabled: New value.

    Returns:
        The stored value.

    Raises:
        ForbiddenError: When the caller is not an administrator.
    """
    _require_admin(principal)
    await _upsert(session, KEY_REGISTER_ENABLED, "true" if enabled else "false")
    await session.commit()
    return enabled


async def get_automation_settings(session: AsyncSession) -> dict[str, bool]:
    """Return hygiene Automation toggles (default off when unseeded)."""
    supersedes_raw = await _get_raw(session, KEY_AUTOMATION_RESOLVE_SUPERSEDES)
    near_dups_raw = await _get_raw(session, KEY_AUTOMATION_RESOLVE_NEAR_DUPS)
    propose_raw = await _get_raw(session, KEY_AUTOMATION_PROPOSE_CONSOLIDATIONS)
    apply_raw = await _get_raw(session, KEY_AUTOMATION_AUTO_APPLY_CONSOLIDATIONS)
    return {
        "resolve_supersedes": (False if supersedes_raw is None else _parse_bool(supersedes_raw)),
        "resolve_near_dups": False if near_dups_raw is None else _parse_bool(near_dups_raw),
        "propose_consolidations": False if propose_raw is None else _parse_bool(propose_raw),
        "auto_apply_consolidations": False if apply_raw is None else _parse_bool(apply_raw),
    }


async def set_automation_settings(
    session: AsyncSession,
    principal: Principal,
    *,
    resolve_supersedes: bool | None = None,
    resolve_near_dups: bool | None = None,
    propose_consolidations: bool | None = None,
    auto_apply_consolidations: bool | None = None,
) -> dict[str, bool]:
    """Admin-only update for hygiene Automation toggles.

    Omitted fields are left unchanged.

    Args:
        session: Database session.
        principal: Caller (must be administrator).
        resolve_supersedes: When true, auto-forget superseded conflict targets.
        resolve_near_dups: When true, auto-forget the older near-duplicate.
        propose_consolidations: When true, surface merge proposals in hygiene.
        auto_apply_consolidations: When true, auto-apply merge proposals.

    Returns:
        The stored values.

    Raises:
        ForbiddenError: When the caller is not an administrator.
    """
    _require_admin(principal)
    current = await get_automation_settings(session)
    next_supersedes = (
        current["resolve_supersedes"] if resolve_supersedes is None else resolve_supersedes
    )
    next_near_dups = (
        current["resolve_near_dups"] if resolve_near_dups is None else resolve_near_dups
    )
    next_propose = (
        current["propose_consolidations"]
        if propose_consolidations is None
        else propose_consolidations
    )
    next_apply = (
        current["auto_apply_consolidations"]
        if auto_apply_consolidations is None
        else auto_apply_consolidations
    )
    await _upsert(
        session,
        KEY_AUTOMATION_RESOLVE_SUPERSEDES,
        "true" if next_supersedes else "false",
    )
    await _upsert(
        session,
        KEY_AUTOMATION_RESOLVE_NEAR_DUPS,
        "true" if next_near_dups else "false",
    )
    await _upsert(
        session,
        KEY_AUTOMATION_PROPOSE_CONSOLIDATIONS,
        "true" if next_propose else "false",
    )
    await _upsert(
        session,
        KEY_AUTOMATION_AUTO_APPLY_CONSOLIDATIONS,
        "true" if next_apply else "false",
    )
    await session.commit()
    return {
        "resolve_supersedes": next_supersedes,
        "resolve_near_dups": next_near_dups,
        "propose_consolidations": next_propose,
        "auto_apply_consolidations": next_apply,
    }


def _env_mail_fallback(settings: Settings) -> MailSettings:
    return MailSettings(
        host=settings.smtp_host.strip(),
        port=settings.smtp_port,
        username=settings.smtp_username.strip(),
        password=settings.smtp_password,
        from_address=settings.smtp_from.strip(),
        use_tls=settings.smtp_use_tls,
    )


async def get_mail_settings(
    session: AsyncSession, settings: Settings | None = None
) -> MailSettings:
    """Load outbound mail settings from the DB, falling back to env when unseeded.

    The password is decrypted when stored as ``enc:v1:…``. An empty password
    row means no password.

    Args:
        session: Database session.
        settings: Optional settings override (also used for decryption).

    Returns:
        Resolved mail settings (may be unconfigured).
    """
    settings = settings or get_settings()
    rows = {
        row.key: row.value
        for row in (
            await session.execute(
                select(InstanceSetting).where(InstanceSetting.key.in_(_SMTP_KEYS))
            )
        ).scalars()
    }
    if not rows:
        return _env_mail_fallback(settings)

    host = rows.get(KEY_SMTP_HOST)
    if host is None:
        host = settings.smtp_host
    from_address = rows.get(KEY_SMTP_FROM)
    if from_address is None:
        from_address = settings.smtp_from
    username = rows.get(KEY_SMTP_USERNAME)
    if username is None:
        username = settings.smtp_username

    port_raw = rows.get(KEY_SMTP_PORT)
    if port_raw is None:
        port = settings.smtp_port
    else:
        try:
            port = int(port_raw.strip())
        except ValueError:
            port = settings.smtp_port

    tls_raw = rows.get(KEY_SMTP_USE_TLS)
    use_tls = settings.smtp_use_tls if tls_raw is None else _parse_bool(tls_raw)

    password_raw = rows.get(KEY_SMTP_PASSWORD)
    if password_raw is None:
        password = settings.smtp_password
    elif not password_raw:
        password = ""
    elif is_encrypted_secret(password_raw):
        password = decrypt_secret(password_raw, settings)
    else:
        # Legacy/plain seed: treat as plaintext once (should not happen after seed).
        password = password_raw

    return MailSettings(
        host=host.strip(),
        port=port,
        username=username.strip(),
        password=password,
        from_address=from_address.strip(),
        use_tls=use_tls,
    )


async def is_mail_configured(session: AsyncSession) -> bool:
    """True when System Settings (or env fallback) have a usable SMTP host and From."""
    return (await get_mail_settings(session)).configured


async def set_mail_settings(
    session: AsyncSession,
    principal: Principal,
    *,
    host: str | None = None,
    port: int | None = None,
    username: str | None = None,
    password: str | None = None,
    clear_password: bool = False,
    from_address: str | None = None,
    use_tls: bool | None = None,
) -> dict[str, Any]:
    """Admin-only update for outbound mail settings.

    Omitted fields are left unchanged. ``password=None`` with ``clear_password``
    false leaves the stored password alone; ``clear_password`` true wipes it;
    a non-None ``password`` replaces it (encrypted).

    Args:
        session: Database session.
        principal: Caller (must be administrator).
        host: SMTP hostname.
        port: SMTP port.
        username: SMTP auth user.
        password: New SMTP password (optional).
        clear_password: When True, clear the stored password.
        from_address: From address.
        use_tls: Whether to use STARTTLS.

    Returns:
        Public mail settings payload (no password secret).

    Raises:
        ForbiddenError: When the caller is not an administrator.
    """
    _require_admin(principal)
    settings = get_settings()
    if host is not None:
        await _upsert(session, KEY_SMTP_HOST, host.strip())
    if port is not None:
        await _upsert(session, KEY_SMTP_PORT, str(port))
    if username is not None:
        await _upsert(session, KEY_SMTP_USERNAME, username.strip())
    if from_address is not None:
        await _upsert(session, KEY_SMTP_FROM, from_address.strip())
    if use_tls is not None:
        await _upsert(session, KEY_SMTP_USE_TLS, "true" if use_tls else "false")
    if clear_password:
        await _upsert(session, KEY_SMTP_PASSWORD, "")
    elif password is not None:
        await _upsert(
            session,
            KEY_SMTP_PASSWORD,
            encrypt_secret(password, settings) if password else "",
        )
    await session.commit()
    return await mail_settings_public(session)


async def mail_settings_public(session: AsyncSession) -> dict[str, Any]:
    """Admin-facing mail settings without the password secret."""
    mail = await get_mail_settings(session)
    return {
        "host": mail.host,
        "port": mail.port,
        "username": mail.username,
        "from_address": mail.from_address,
        "use_tls": mail.use_tls,
        "configured": mail.configured,
        "password_set": mail.password_set,
    }


@dataclass(frozen=True, slots=True)
class WebhookSettings:
    """Resolved outbound webhook configuration."""

    enabled: bool
    url: str
    secret: str
    fail_count: int

    @property
    def secret_set(self) -> bool:
        return bool(self.secret)


async def get_webhook_settings(session: AsyncSession) -> WebhookSettings:
    """Load webhook settings from the database."""
    enabled_raw = await _get_raw(session, KEY_WEBHOOK_ENABLED)
    url_raw = await _get_raw(session, KEY_WEBHOOK_URL)
    secret_raw = await _get_raw(session, KEY_WEBHOOK_SECRET)
    fail_raw = await _get_raw(session, KEY_WEBHOOK_FAIL_COUNT)
    settings = get_settings()
    secret = ""
    if secret_raw and is_encrypted_secret(secret_raw):
        secret = decrypt_secret(secret_raw, settings)
    elif secret_raw:
        secret = secret_raw
    fail_count = 0
    if fail_raw is not None:
        try:
            fail_count = int(fail_raw.strip())
        except ValueError:
            fail_count = 0
    return WebhookSettings(
        enabled=False if enabled_raw is None else _parse_bool(enabled_raw),
        url=(url_raw or "").strip(),
        secret=secret,
        fail_count=fail_count,
    )


async def webhook_settings_public(session: AsyncSession) -> dict[str, Any]:
    """Admin-facing webhook settings without the secret."""
    hook = await get_webhook_settings(session)
    return {
        "enabled": hook.enabled,
        "url": hook.url,
        "fail_count": hook.fail_count,
        "secret_set": hook.secret_set,
    }


async def set_webhook_settings(
    session: AsyncSession,
    principal: Principal,
    *,
    enabled: bool | None = None,
    url: str | None = None,
    secret: str | None = None,
    clear_secret: bool = False,
) -> dict[str, Any]:
    """Admin-only update for webhook delivery settings."""
    _require_admin(principal)
    settings = get_settings()
    current = await get_webhook_settings(session)
    next_enabled = current.enabled if enabled is None else enabled
    next_url = current.url if url is None else url.strip()
    await _upsert(session, KEY_WEBHOOK_ENABLED, "true" if next_enabled else "false")
    await _upsert(session, KEY_WEBHOOK_URL, next_url)
    if clear_secret:
        await _upsert(session, KEY_WEBHOOK_SECRET, "")
    elif secret is not None:
        await _upsert(
            session,
            KEY_WEBHOOK_SECRET,
            encrypt_secret(secret, settings) if secret else "",
        )
    await session.commit()
    return await webhook_settings_public(session)


async def increment_webhook_fail_count(session: AsyncSession) -> None:
    """Bump the failed-delivery counter (admin session, own commit)."""
    hook = await get_webhook_settings(session)
    await _upsert(session, KEY_WEBHOOK_FAIL_COUNT, str(hook.fail_count + 1))
    await session.commit()
