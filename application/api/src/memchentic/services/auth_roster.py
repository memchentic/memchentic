"""Auth group rosters: membership primitives, startup sync and IdP sync."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Collection

from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.users import get_user, normalize_email, revoke_credentials_raw
from memchentic.models.entities import AuthGroup, AuthGroupMember, InstanceSetting, User
from memchentic.services.instance_settings import get_register_enabled
from memchentic.settings import Settings, get_settings

logger = logging.getLogger("memchentic.auth_groups")


SLUG_ADMINISTRATORS = "administrators"


SLUG_USERS = "users"


SOURCE_MANUAL = "manual"


SOURCE_IDP = "idp"


# instance_settings row: user id of the account that ADMIN_EMAIL named at the
# last ensure_auth_groups run.
KEY_ENV_ADMIN_USER_ID = "env_admin_user_id"


SYSTEM_GROUPS: tuple[tuple[str, str], ...] = (
    (SLUG_ADMINISTRATORS, "Administrators"),
    (SLUG_USERS, "Users"),
)


async def get_by_slug(session: AsyncSession, slug: str) -> AuthGroup | None:
    result = await session.execute(select(AuthGroup).where(AuthGroup.slug == slug))
    return result.scalar_one_or_none()


async def _ensure_group(session: AsyncSession, slug: str, name: str) -> AuthGroup:
    group = await get_by_slug(session, slug)
    if group:
        if group.name != name:
            group.name = name
        return group
    group = AuthGroup(id=uuid.uuid4(), slug=slug, name=name, system=True)
    session.add(group)
    await session.flush()
    return group


async def is_member(session: AsyncSession, group_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    result = await session.execute(
        select(AuthGroupMember).where(
            AuthGroupMember.group_id == group_id, AuthGroupMember.user_id == user_id
        )
    )
    return result.scalar_one_or_none() is not None


async def remove_member_raw(session: AsyncSession, group_id: uuid.UUID, user_id: uuid.UUID) -> None:
    await session.execute(
        delete(AuthGroupMember).where(
            AuthGroupMember.group_id == group_id, AuthGroupMember.user_id == user_id
        )
    )


def is_env_admin(user: User, settings: Settings) -> bool:
    return normalize_email(user.email) == normalize_email(settings.admin_contact_email)


def group_bound(group: AuthGroup) -> bool:
    return bool(group.idp_group_id and group.idp_group_id.strip())


async def user_in_idp_bound_roster(session: AsyncSession, user_id: uuid.UUID) -> bool:
    """True when IdP sync placed ``user_id`` on a currently bound Auth Group.

    Used to refuse hard-delete of directory users that IdP sync owns: the
    next IdP sign-in would create them again. A member that was added by hand
    before the bind was set (``source == "manual"``) is not owned by the IdP
    and stays deletable.

    Args:
        session: Database session.
        user_id: Target user id.

    Returns:
        Whether the user has an IdP-sourced row on a bound roster.
    """
    result = await session.execute(
        select(AuthGroup.id)
        .join(AuthGroupMember, AuthGroupMember.group_id == AuthGroup.id)
        .where(
            AuthGroupMember.user_id == user_id,
            AuthGroupMember.source == SOURCE_IDP,
            AuthGroup.idp_group_id.is_not(None),
            AuthGroup.idp_group_id != "",
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


def _bind_matches(group: AuthGroup, idp_groups: Collection[str]) -> bool:
    if not group_bound(group):
        return False
    gid = (group.idp_group_id or "").strip()
    gname = (group.idp_group_name or "").strip()
    normalized = {g.strip() for g in idp_groups if g and str(g).strip()}
    if gid and gid in normalized:
        return True
    return bool(gname and gname in normalized)


async def add_member_raw(
    session: AsyncSession,
    group: AuthGroup,
    user_id: uuid.UUID,
    *,
    source: str = SOURCE_MANUAL,
) -> AuthGroupMember:
    """Idempotent membership insert (no ACL)."""
    existing = await session.execute(
        select(AuthGroupMember).where(
            AuthGroupMember.group_id == group.id, AuthGroupMember.user_id == user_id
        )
    )
    row = existing.scalar_one_or_none()
    if row:
        if row.source != source:
            row.source = source
        return row
    row = AuthGroupMember(group_id=group.id, user_id=user_id, source=source)
    session.add(row)
    await session.flush()
    return row


async def _track_env_admin(session: AsyncSession, administrators: AuthGroup, admin: User) -> None:
    """Demote the previous break-glass account when ``ADMIN_EMAIL`` changed.

    The id of the current break-glass account is stored in
    ``instance_settings``. When the stored id differs from ``admin.id``:

    * the previous account loses its Administrators row (the Users backfill
      in ``ensure_auth_groups`` places it in Users when that roster is
      unbound);
    * sessions, refresh tokens and API keys that ``admin`` held before it
      became the break-glass account are revoked, so credentials issued to
      an ordinary account do not become administrator credentials.

    Nothing is revoked on the first run (no stored id yet).

    Args:
        session: Database session. The caller of ``ensure_auth_groups`` ran
            ``ensure_admin_user`` just before, which leaves the RLS context
            of the admin user; that context can see the admin's own
            ``api_keys`` rows.
        administrators: The Administrators auth group.
        admin: Current break-glass user.
    """
    row = await session.get(InstanceSetting, KEY_ENV_ADMIN_USER_ID)
    previous: uuid.UUID | None = None
    if row is not None:
        try:
            previous = uuid.UUID(row.value)
        except ValueError:
            previous = None
    if previous == admin.id:
        return
    if previous is not None:
        logger.warning(
            "ADMIN_EMAIL changed: removing user %s from Administrators and "
            "revoking earlier credentials of the new break-glass user %s",
            previous,
            admin.id,
        )
        await remove_member_raw(session, administrators.id, previous)
        await revoke_credentials_raw(session, admin.id)
    if row is None:
        session.add(InstanceSetting(key=KEY_ENV_ADMIN_USER_ID, value=str(admin.id)))
    else:
        row.value = str(admin.id)
    await session.flush()


async def ensure_auth_groups(session: AsyncSession, admin: User) -> dict[str, AuthGroup]:
    """Ensure system Auth Groups with exclusive membership.

    ``ADMIN_EMAIL`` is always in Administrators and never in Users. Unbound
    Users roster is backfilled for non-admins; bound rosters are IdP-synced only.

    Args:
        session: Database session.
        admin: The ``ADMIN_EMAIL`` user row, already created by the caller.

    Returns:
        Map of slug → auth group.
    """
    groups: dict[str, AuthGroup] = {}
    for slug, name in SYSTEM_GROUPS:
        groups[slug] = await _ensure_group(session, slug, name)

    await _track_env_admin(session, groups[SLUG_ADMINISTRATORS], admin)
    await add_member_raw(session, groups[SLUG_ADMINISTRATORS], admin.id, source=SOURCE_MANUAL)
    await remove_member_raw(session, groups[SLUG_USERS].id, admin.id)

    admin_ids = (
        (
            await session.execute(
                select(AuthGroupMember.user_id).where(
                    AuthGroupMember.group_id == groups[SLUG_ADMINISTRATORS].id
                )
            )
        )
        .scalars()
        .all()
    )
    admin_id_set = set(admin_ids)

    if admin_id_set:
        await session.execute(
            delete(AuthGroupMember).where(
                AuthGroupMember.group_id == groups[SLUG_USERS].id,
                AuthGroupMember.user_id.in_(admin_id_set),
            )
        )

    # Only backfill Users when that roster is unbound. Set-based: the cost
    # does not grow with the number of users.
    if not group_bound(groups[SLUG_USERS]):
        await session.execute(
            text(
                """
                INSERT INTO auth_group_members (group_id, user_id, source)
                SELECT CAST(:users_gid AS uuid), u.id, CAST(:manual AS varchar)
                FROM users u
                WHERE NOT EXISTS (
                  SELECT 1 FROM auth_group_members m
                  WHERE m.user_id = u.id
                    AND m.group_id IN (CAST(:users_gid AS uuid), CAST(:admins_gid AS uuid))
                )
                ON CONFLICT (group_id, user_id) DO NOTHING
                """
            ),
            {
                "users_gid": groups[SLUG_USERS].id,
                "admins_gid": groups[SLUG_ADMINISTRATORS].id,
                "manual": SOURCE_MANUAL,
            },
        )
        # An unbound roster has only manual members.
        await session.execute(
            update(AuthGroupMember)
            .where(
                AuthGroupMember.group_id == groups[SLUG_USERS].id,
                AuthGroupMember.source != SOURCE_MANUAL,
            )
            .values(source=SOURCE_MANUAL)
        )

    await session.commit()
    return groups


async def user_is_administrator(session: AsyncSession, user_id: uuid.UUID) -> bool:
    """True when ``user_id`` is in the Administrators auth group."""
    group = await get_by_slug(session, SLUG_ADMINISTRATORS)
    if not group:
        return False
    return await is_member(session, group.id, user_id)


async def add_to_users_group(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Place a newly created account in Users when that roster is unbound."""
    settings = get_settings()
    user = await get_user(session, user_id)
    if user and is_env_admin(user, settings):
        return
    users = await _ensure_group(session, SLUG_USERS, "Users")
    if group_bound(users):
        return
    administrators = await _ensure_group(session, SLUG_ADMINISTRATORS, "Administrators")
    await remove_member_raw(session, administrators.id, user_id)
    await add_member_raw(session, users, user_id, source=SOURCE_MANUAL)


async def idp_signup_allowed(session: AsyncSession, idp_groups: Collection[str]) -> bool:
    """Decide whether a first-time IdP sign-in may create a local account.

    First match wins: a match on the Administrators bind admits the person; a
    bound Users roster admits only members of the bound IdP group; otherwise
    the instance registration toggle decides.

    Args:
        session: Database session.
        idp_groups: Group ids/names from the IdP login.

    Returns:
        True when a new account may be created.
    """
    groups = list(idp_groups)
    administrators = await get_by_slug(session, SLUG_ADMINISTRATORS)
    if administrators is not None and _bind_matches(administrators, groups):
        return True
    users = await get_by_slug(session, SLUG_USERS)
    if users is not None and group_bound(users):
        return _bind_matches(users, groups)
    return await get_register_enabled(session)


async def sync_user_from_idp_groups(
    session: AsyncSession,
    user: User,
    idp_groups: Collection[str],
    *,
    settings: Settings | None = None,
) -> None:
    """Apply Auth Group membership from IdP group claims (Administrators wins).

    Does not commit; the caller owns the transaction.

    Args:
        session: Database session.
        user: Local user row.
        idp_groups: Group ids/names from the IdP login.
        settings: Optional settings override.
    """
    settings = settings or get_settings()
    administrators = await _ensure_group(session, SLUG_ADMINISTRATORS, "Administrators")
    users = await _ensure_group(session, SLUG_USERS, "Users")

    if is_env_admin(user, settings):
        await add_member_raw(session, administrators, user.id, source=SOURCE_MANUAL)
        await remove_member_raw(session, users.id, user.id)
        return

    admin_match = _bind_matches(administrators, idp_groups)
    users_match = _bind_matches(users, idp_groups)

    if admin_match:
        await remove_member_raw(session, users.id, user.id)
        await add_member_raw(session, administrators, user.id, source=SOURCE_IDP)
    elif users_match:
        await remove_member_raw(session, administrators.id, user.id)
        await add_member_raw(session, users, user.id, source=SOURCE_IDP)
    else:
        # Drop IdP memberships that no longer match; seed Users if unbound.
        if group_bound(administrators):
            mem = await session.execute(
                select(AuthGroupMember).where(
                    AuthGroupMember.group_id == administrators.id,
                    AuthGroupMember.user_id == user.id,
                    AuthGroupMember.source == SOURCE_IDP,
                )
            )
            row = mem.scalar_one_or_none()
            if row:
                await session.delete(row)
        if group_bound(users):
            mem = await session.execute(
                select(AuthGroupMember).where(
                    AuthGroupMember.group_id == users.id,
                    AuthGroupMember.user_id == user.id,
                    AuthGroupMember.source == SOURCE_IDP,
                )
            )
            row = mem.scalar_one_or_none()
            if row:
                await session.delete(row)
        elif not await user_is_administrator(session, user.id):
            await add_member_raw(session, users, user.id, source=SOURCE_MANUAL)
