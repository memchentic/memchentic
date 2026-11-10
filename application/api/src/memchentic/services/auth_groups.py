"""Fixed Auth Groups (Administrators / Users) — not memory spaces."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, TypedDict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from memchentic.auth.users import (
    get_user,
    get_user_by_email,
)
from memchentic.models.entities import AuthGroup, AuthGroupMember
from memchentic.services import limits
from memchentic.services.auth_roster import (
    SLUG_ADMINISTRATORS,
    SLUG_USERS,
    SOURCE_IDP,
    SOURCE_MANUAL,
    SYSTEM_GROUPS,
    add_member_raw,
    ensure_auth_groups,
    get_by_slug,
    group_bound,
    is_env_admin,
    is_member,
    remove_member_raw,
)
from memchentic.services.errors import ForbiddenError, NotFoundError
from memchentic.services.validation import check_optional_text
from memchentic.settings import get_settings

if TYPE_CHECKING:
    from memchentic.auth.principal import Principal


class AuthGroupOut(TypedDict):
    """One auth group as returned by the admin API."""

    id: str
    slug: str
    name: str
    system: bool
    member_count: int
    roster_bound: bool
    idp_group_id: str | None
    idp_group_name: str | None


class AuthGroupMemberOut(TypedDict):
    """One auth group member as returned by the admin API."""

    user_id: str
    email: str | None
    display_name: str | None
    locked: bool
    source: str


class IdpBindOut(TypedDict):
    """The IdP bind of one auth group."""

    slug: str
    idp_group_id: str | None
    idp_group_name: str | None
    roster_bound: bool


async def idp_bound_user_ids(session: AsyncSession) -> set[uuid.UUID]:
    """Ids of all users that IdP sync placed on a currently bound auth group.

    Set-based form of ``user_in_idp_bound_roster`` for list endpoints.

    Args:
        session: Database session.

    Returns:
        User ids with an IdP-sourced row on a bound Administrators or Users roster.
    """
    result = await session.execute(
        select(AuthGroupMember.user_id)
        .join(AuthGroup, AuthGroup.id == AuthGroupMember.group_id)
        .where(
            AuthGroupMember.source == SOURCE_IDP,
            AuthGroup.idp_group_id.is_not(None),
            AuthGroup.idp_group_id != "",
        )
        .distinct()
    )
    return set(result.scalars())


def _require_admin(principal: Principal) -> None:
    if not getattr(principal, "is_admin", False):
        raise ForbiddenError("Administrators only")


def _group_payload(group: AuthGroup, member_count: int) -> AuthGroupOut:
    bound = group_bound(group)
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


async def list_groups(session: AsyncSession, principal: Principal) -> list[AuthGroupOut]:
    """List fixed Auth Groups with member counts and bind state."""
    _require_admin(principal)
    rows: list[AuthGroupOut] = []
    for slug, _name in SYSTEM_GROUPS:
        group = await get_by_slug(session, slug)
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
) -> list[AuthGroupMemberOut]:
    """List members of a fixed Auth Group."""
    _require_admin(principal)
    group = await get_by_slug(session, slug)
    if not group:
        raise NotFoundError("Auth group not found")
    result = await session.execute(
        select(AuthGroupMember)
        .options(selectinload(AuthGroupMember.user))
        .where(AuthGroupMember.group_id == group.id)
        .order_by(AuthGroupMember.created_at.asc())
    )
    members = list(result.scalars())
    settings = get_settings()
    roster_bound = group_bound(group)
    out: list[AuthGroupMemberOut] = []
    for m in members:
        user = m.user
        env_locked = (
            group.slug == SLUG_ADMINISTRATORS and user is not None and is_env_admin(user, settings)
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


async def get_idp_bind(session: AsyncSession, principal: Principal, slug: str) -> IdpBindOut:
    """Return the IdP bind for one Auth Group."""
    _require_admin(principal)
    group = await get_by_slug(session, slug)
    if not group:
        raise NotFoundError("Auth group not found")
    bound = group_bound(group)
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
) -> IdpBindOut:
    """Set or clear the IdP group bind for an Auth Group.

    Clearing the bind restores manual membership. Setting a bind makes the
    roster sync-only (no manual add/remove).
    """
    _require_admin(principal)
    group = await get_by_slug(session, slug)
    if not group:
        raise NotFoundError("Auth group not found")
    gid = (idp_group_id or "").strip() or None
    gname = (idp_group_name or "").strip() or None
    gid = check_optional_text(gid, "idp_group_id", limits.IDP_GROUP_MAX)
    gname = check_optional_text(gname, "idp_group_name", limits.IDP_GROUP_MAX)
    if gid:
        group.idp_group_id = gid
        group.idp_group_name = gname or gid
    else:
        group.idp_group_id = None
        group.idp_group_name = None
    await session.commit()
    if gid is None:
        # The roster is manual again: place users that the IdP sync left out.
        admin = await get_user_by_email(session, get_settings().admin_contact_email)
        if admin is not None:
            await ensure_auth_groups(session, admin)
    return await get_idp_bind(session, principal, slug)


async def add_member(
    session: AsyncSession,
    principal: Principal,
    slug: str,
    user_id: uuid.UUID,
) -> AuthGroupMemberOut:
    """Add a user to a fixed Auth Group (exclusive — drops the other roster)."""
    _require_admin(principal)
    group = await get_by_slug(session, slug)
    if not group:
        raise NotFoundError("Auth group not found")
    if group_bound(group):
        raise ForbiddenError("Roster is synced from IdP — clear the bind to edit manually")
    user = await get_user(session, user_id)
    if not user:
        raise NotFoundError("User not found")
    settings = get_settings()
    if slug == SLUG_USERS and is_env_admin(user, settings):
        raise ForbiddenError("Admin account stays in Administrators only")

    other_slug = SLUG_USERS if slug == SLUG_ADMINISTRATORS else SLUG_ADMINISTRATORS
    other = await get_by_slug(session, other_slug)
    if other:
        if other_slug == SLUG_ADMINISTRATORS and is_env_admin(user, settings):
            raise ForbiddenError("Cannot move the admin account out of Administrators")
        if (
            group_bound(other)
            and other_slug == SLUG_ADMINISTRATORS
            and await is_member(session, other.id, user_id)
        ):
            raise ForbiddenError("Cannot move members out of an IdP-bound Administrators roster")
        await remove_member_raw(session, other.id, user_id)

    await add_member_raw(session, group, user_id, source=SOURCE_MANUAL)
    await session.commit()
    locked = group.slug == SLUG_ADMINISTRATORS and is_env_admin(user, settings)
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
        ForbiddenError: When the roster is IdP-bound or removing the env admin.
        NotFoundError: Unknown group or membership.
    """
    _require_admin(principal)
    group = await get_by_slug(session, slug)
    if not group:
        raise NotFoundError("Auth group not found")
    if group_bound(group):
        raise ForbiddenError("Roster is synced from IdP — clear the bind to edit manually")
    user = await get_user(session, user_id)
    if not user:
        raise NotFoundError("User not found")
    settings = get_settings()
    if group.slug == SLUG_ADMINISTRATORS and is_env_admin(user, settings):
        raise ForbiddenError("Cannot remove the admin account from Administrators")

    result = await session.execute(
        select(AuthGroupMember).where(
            AuthGroupMember.group_id == group.id, AuthGroupMember.user_id == user_id
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise NotFoundError("Membership not found")
    await session.delete(row)

    if group.slug == SLUG_ADMINISTRATORS:
        users = await get_by_slug(session, SLUG_USERS)
        if users and not group_bound(users):
            await add_member_raw(session, users, user_id, source=SOURCE_MANUAL)

    await session.commit()
