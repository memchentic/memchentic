"""Fixed Auth Groups (Administrators / Users) — not memory spaces."""

from __future__ import annotations

import uuid
from collections.abc import Collection
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from memchentic.auth.users import ensure_admin_user, get_user, normalize_email
from memchentic.models.entities import AuthGroup, AuthGroupMember, User
from memchentic.settings import Settings, get_settings

if TYPE_CHECKING:
    from memchentic.auth.principal import Principal

SLUG_ADMINISTRATORS = "administrators"
SLUG_USERS = "users"
SOURCE_MANUAL = "manual"
SOURCE_IDP = "idp"

_SYSTEM_GROUPS: tuple[tuple[str, str], ...] = (
    (SLUG_ADMINISTRATORS, "Administrators"),
    (SLUG_USERS, "Users"),
)


async def _get_by_slug(session: AsyncSession, slug: str) -> AuthGroup | None:
    result = await session.execute(select(AuthGroup).where(AuthGroup.slug == slug))
    return result.scalar_one_or_none()


async def _ensure_group(session: AsyncSession, slug: str, name: str) -> AuthGroup:
    group = await _get_by_slug(session, slug)
    if group:
        if group.name != name:
            group.name = name
        return group
    group = AuthGroup(id=uuid.uuid4(), slug=slug, name=name, system=True)
    session.add(group)
    await session.flush()
    return group


async def _is_member(session: AsyncSession, group_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    result = await session.execute(
        select(AuthGroupMember).where(
            AuthGroupMember.group_id == group_id, AuthGroupMember.user_id == user_id
        )
    )
    return result.scalar_one_or_none() is not None


async def _remove_member_raw(
    session: AsyncSession, group_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    await session.execute(
        delete(AuthGroupMember).where(
            AuthGroupMember.group_id == group_id, AuthGroupMember.user_id == user_id
        )
    )


def _is_env_admin(user: User, settings: Settings) -> bool:
    return normalize_email(user.email) == normalize_email(settings.admin_contact_email)


def _group_bound(group: AuthGroup) -> bool:
    return bool(group.idp_group_id and group.idp_group_id.strip())


async def user_in_idp_bound_roster(session: AsyncSession, user_id: uuid.UUID) -> bool:
    """True when ``user_id`` is a member of any Auth Group with an IdP bind.

    Used to refuse hard-delete of directory users that IdP sync owns.

    Args:
        session: Database session.
        user_id: Target user id.

    Returns:
        Whether the user sits on a currently bound Administrators or Users roster.
    """
    result = await session.execute(
        select(AuthGroup.id)
        .join(AuthGroupMember, AuthGroupMember.group_id == AuthGroup.id)
        .where(
            AuthGroupMember.user_id == user_id,
            AuthGroup.idp_group_id.is_not(None),
            AuthGroup.idp_group_id != "",
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


def _bind_matches(group: AuthGroup, idp_groups: Collection[str]) -> bool:
    if not _group_bound(group):
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


async def ensure_auth_groups(
    session: AsyncSession, settings: Settings | None = None
) -> dict[str, AuthGroup]:
    """Ensure system Auth Groups with exclusive membership.

    ``ADMIN_EMAIL`` is always in Administrators and never in Users. Unbound
    Users roster is backfilled for non-admins; bound rosters are IdP-synced only.

    Args:
        session: Database session.
        settings: Settings override.

    Returns:
        Map of slug → auth group.
    """
    settings = settings or get_settings()
    groups: dict[str, AuthGroup] = {}
    for slug, name in _SYSTEM_GROUPS:
        groups[slug] = await _ensure_group(session, slug, name)

    admin = await ensure_admin_user(session, settings)
    await add_member_raw(session, groups[SLUG_ADMINISTRATORS], admin.id, source=SOURCE_MANUAL)
    await _remove_member_raw(session, groups[SLUG_USERS].id, admin.id)

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

    # Only backfill Users when that roster is unbound.
    if not _group_bound(groups[SLUG_USERS]):
        user_ids = (await session.execute(select(User.id))).scalars().all()
        for uid in user_ids:
            if uid in admin_id_set:
                continue
            await add_member_raw(session, groups[SLUG_USERS], uid, source=SOURCE_MANUAL)

    await session.commit()
    return groups


async def user_is_administrator(session: AsyncSession, user_id: uuid.UUID) -> bool:
    """True when ``user_id`` is in the Administrators auth group."""
    group = await _get_by_slug(session, SLUG_ADMINISTRATORS)
    if not group:
        return False
    return await _is_member(session, group.id, user_id)


async def add_to_users_group(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Place a newly created account in Users when that roster is unbound."""
    settings = get_settings()
    user = await get_user(session, user_id)
    if user and _is_env_admin(user, settings):
        return
    users = await _ensure_group(session, SLUG_USERS, "Users")
    if _group_bound(users):
        return
    administrators = await _ensure_group(session, SLUG_ADMINISTRATORS, "Administrators")
    await _remove_member_raw(session, administrators.id, user_id)
    await add_member_raw(session, users, user_id, source=SOURCE_MANUAL)


def _require_admin(principal: Principal) -> None:
    if not getattr(principal, "is_admin", False):
        raise PermissionError("Administrators only")


def _group_payload(group: AuthGroup, member_count: int) -> dict[str, Any]:
    bound = _group_bound(group)
    return {
        "id": str(group.id),
        "slug": group.slug,
        "name": group.name,
        "system": group.system,
        "member_count": member_count,
        "roster_bound": bound,
        "idp_group_id": group.idp_group_id if bound else None,
        "idp_group_name": group.idp_group_name if bound else None,
    }


async def list_groups(session: AsyncSession, principal: Principal) -> list[dict[str, Any]]:
    """List fixed Auth Groups with member counts and bind state."""
    _require_admin(principal)
    await ensure_auth_groups(session)
    rows: list[dict[str, Any]] = []
    for slug, _name in _SYSTEM_GROUPS:
        group = await _get_by_slug(session, slug)
        if not group:
            continue
        count = await session.scalar(
            select(func.count())
            .select_from(AuthGroupMember)
            .where(AuthGroupMember.group_id == group.id)
        )
        rows.append(_group_payload(group, int(count or 0)))
    return rows


async def list_members(
    session: AsyncSession, principal: Principal, slug: str
) -> list[dict[str, Any]]:
    """List members of a fixed Auth Group."""
    _require_admin(principal)
    group = await _get_by_slug(session, slug)
    if not group:
        raise LookupError("Auth group not found")
    result = await session.execute(
        select(AuthGroupMember)
        .options(selectinload(AuthGroupMember.user))
        .where(AuthGroupMember.group_id == group.id)
        .order_by(AuthGroupMember.created_at.asc())
    )
    members = list(result.scalars())
    settings = get_settings()
    roster_bound = _group_bound(group)
    out: list[dict[str, Any]] = []
    for m in members:
        user = m.user
        env_locked = (
            group.slug == SLUG_ADMINISTRATORS and user is not None and _is_env_admin(user, settings)
        )
        locked = env_locked or roster_bound
        out.append(
            {
                "user_id": str(m.user_id),
                "email": user.email if user else None,
                "display_name": user.display_name if user else None,
                "locked": locked,
                "source": m.source,
            }
        )
    return out


async def get_idp_bind(session: AsyncSession, principal: Principal, slug: str) -> dict[str, Any]:
    """Return the IdP bind for one Auth Group."""
    _require_admin(principal)
    group = await _get_by_slug(session, slug)
    if not group:
        raise LookupError("Auth group not found")
    bound = _group_bound(group)
    return {
        "slug": group.slug,
        "idp_group_id": group.idp_group_id if bound else None,
        "idp_group_name": group.idp_group_name if bound else None,
        "roster_bound": bound,
    }


async def set_idp_bind(
    session: AsyncSession,
    principal: Principal,
    slug: str,
    *,
    idp_group_id: str | None,
    idp_group_name: str | None,
) -> dict[str, Any]:
    """Set or clear the IdP group bind for an Auth Group.

    Clearing the bind restores manual membership. Setting a bind makes the
    roster sync-only (no manual add/remove).
    """
    _require_admin(principal)
    group = await _get_by_slug(session, slug)
    if not group:
        raise LookupError("Auth group not found")
    gid = (idp_group_id or "").strip() or None
    gname = (idp_group_name or "").strip() or None
    if gid:
        group.idp_group_id = gid
        group.idp_group_name = gname or gid
    else:
        group.idp_group_id = None
        group.idp_group_name = None
    await session.commit()
    return await get_idp_bind(session, principal, slug)


async def sync_user_from_idp_groups(
    session: AsyncSession,
    user: User,
    idp_groups: Collection[str],
    *,
    settings: Settings | None = None,
    commit: bool = True,
) -> None:
    """Apply Auth Group membership from IdP group claims (Administrators wins).

    Args:
        session: Database session.
        user: Local user row.
        idp_groups: Group ids/names from the IdP login.
        settings: Optional settings override.
        commit: Whether to commit (False when caller commits).
    """
    settings = settings or get_settings()
    administrators = await _ensure_group(session, SLUG_ADMINISTRATORS, "Administrators")
    users = await _ensure_group(session, SLUG_USERS, "Users")

    if _is_env_admin(user, settings):
        await add_member_raw(session, administrators, user.id, source=SOURCE_MANUAL)
        await _remove_member_raw(session, users.id, user.id)
        if commit:
            await session.commit()
        return

    admin_match = _bind_matches(administrators, idp_groups)
    users_match = _bind_matches(users, idp_groups)

    if admin_match:
        await _remove_member_raw(session, users.id, user.id)
        await add_member_raw(session, administrators, user.id, source=SOURCE_IDP)
    elif users_match:
        await _remove_member_raw(session, administrators.id, user.id)
        await add_member_raw(session, users, user.id, source=SOURCE_IDP)
    else:
        # Drop IdP memberships that no longer match; seed Users if unbound.
        if _group_bound(administrators):
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
        if _group_bound(users):
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

    if commit:
        await session.commit()


async def add_member(
    session: AsyncSession,
    principal: Principal,
    slug: str,
    user_id: uuid.UUID,
) -> dict[str, Any]:
    """Add a user to a fixed Auth Group (exclusive — drops the other roster)."""
    _require_admin(principal)
    group = await _get_by_slug(session, slug)
    if not group:
        raise LookupError("Auth group not found")
    if _group_bound(group):
        raise PermissionError("Roster is synced from IdP — clear the bind to edit manually")
    user = await get_user(session, user_id)
    if not user:
        raise LookupError("User not found")
    settings = get_settings()
    if slug == SLUG_USERS and _is_env_admin(user, settings):
        raise PermissionError("Admin account stays in Administrators only")

    other_slug = SLUG_USERS if slug == SLUG_ADMINISTRATORS else SLUG_ADMINISTRATORS
    other = await _get_by_slug(session, other_slug)
    if other:
        if other_slug == SLUG_ADMINISTRATORS and _is_env_admin(user, settings):
            raise PermissionError("Cannot move the admin account out of Administrators")
        if _group_bound(other) and other_slug == SLUG_ADMINISTRATORS:
            raise PermissionError("Cannot move members out of an IdP-bound Administrators roster")
        await _remove_member_raw(session, other.id, user_id)

    await add_member_raw(session, group, user_id, source=SOURCE_MANUAL)
    await session.commit()
    locked = group.slug == SLUG_ADMINISTRATORS and _is_env_admin(user, settings)
    return {
        "user_id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "locked": locked,
        "source": SOURCE_MANUAL,
    }


async def remove_member(
    session: AsyncSession,
    principal: Principal,
    slug: str,
    user_id: uuid.UUID,
) -> None:
    """Remove a user from a fixed Auth Group.

    Removing from Administrators places the account in Users when Users is unbound.
    The env admin cannot leave Administrators. Bound rosters refuse all removes.

    Raises:
        PermissionError: When the roster is IdP-bound or removing the env admin.
        LookupError: Unknown group or membership.
    """
    _require_admin(principal)
    group = await _get_by_slug(session, slug)
    if not group:
        raise LookupError("Auth group not found")
    if _group_bound(group):
        raise PermissionError("Roster is synced from IdP — clear the bind to edit manually")
    user = await get_user(session, user_id)
    if not user:
        raise LookupError("User not found")
    settings = get_settings()
    if group.slug == SLUG_ADMINISTRATORS and _is_env_admin(user, settings):
        raise PermissionError("Cannot remove the admin account from Administrators")

    result = await session.execute(
        select(AuthGroupMember).where(
            AuthGroupMember.group_id == group.id, AuthGroupMember.user_id == user_id
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise LookupError("Membership not found")
    await session.delete(row)

    if group.slug == SLUG_ADMINISTRATORS:
        users = await _get_by_slug(session, SLUG_USERS)
        if users and not _group_bound(users):
            await add_member_raw(session, users, user_id, source=SOURCE_MANUAL)

    await session.commit()
