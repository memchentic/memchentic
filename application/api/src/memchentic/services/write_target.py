"""Write-target resolution: which space a new entry or guide is saved to."""

from __future__ import annotations

import uuid
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import Principal
from memchentic.db.session import commit_with_rls
from memchentic.models.entities import Space, SpaceKind, SpaceMember, User
from memchentic.services.errors import ForbiddenError, NotFoundError, ValidationError
from memchentic.services.spaces import ensure_personal_space, get_personal_space, get_readable_space

Visibility = Literal["personal", "shared"]


class SpaceResolveError(ValidationError):
    """A write target (group name or shared default) did not resolve to one group."""


def _group_choices(groups: list[Space]) -> str:
    return ", ".join(f"{g.name} ({g.id})" for g in groups) or "none"


async def _member_groups(session: AsyncSession, principal: Principal) -> list[Space]:
    """Groups the caller belongs to (membership join — not admin instance-wide list)."""
    result = await session.execute(
        select(Space)
        .join(SpaceMember, SpaceMember.space_id == Space.id)
        .where(
            SpaceMember.user_id == principal.user_id,
            SpaceMember.accepted_at.is_not(None),
            Space.archived_at.is_(None),
            Space.kind == SpaceKind.group,
        )
        .order_by(Space.name.asc())
    )
    return list(result.scalars())


async def resolve_group_by_name(session: AsyncSession, principal: Principal, group: str) -> Space:
    """Resolve a group name to exactly one unarchived group the caller belongs to.

    Args:
        session: Database session.
        principal: Authenticated user principal.
        group: Group name; matched case-insensitively and exactly.

    Returns:
        The single matching group.

    Raises:
        SpaceResolveError: When zero or several of the caller's groups match.
    """
    groups = await _member_groups(session, principal)
    wanted = group.strip().casefold()
    matches = [g for g in groups if g.name.strip().casefold() == wanted]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SpaceResolveError(
            f"No group named {group!r} among yours; groups: {_group_choices(groups)}"
        )
    raise SpaceResolveError(
        f"Group name {group!r} is ambiguous; pass space_id. Matches: {_group_choices(matches)}"
    )


async def _resolve_shared_default(session: AsyncSession, principal: Principal) -> Space:
    groups = await _member_groups(session, principal)
    user = await session.get(User, principal.user_id)
    default_id = user.default_shared_space_id if user else None
    for group_space in groups:
        if group_space.id == default_id:
            return group_space
    if len(groups) == 1:
        return groups[0]
    if not groups:
        raise SpaceResolveError("visibility=shared but you are not on any group")
    raise SpaceResolveError(
        "visibility=shared needs group, space_id, or a default shared group; "
        f"groups: {_group_choices(groups)}"
    )


async def resolve_write_space(
    session: AsyncSession,
    principal: Principal,
    space_id: uuid.UUID | None,
    *,
    group: str | None = None,
    visibility: Visibility | None = None,
) -> Space:
    """Pick the space a write lands in.

    Precedence: explicit ``space_id``, then ``group`` name, then
    ``visibility="shared"`` (default shared group, else the sole group), else
    the caller's personal space.

    Args:
        session: Database session.
        principal: Authenticated caller.
        space_id: Explicit target; skips name and default resolution.
        group: Group name to resolve among the caller's memberships.
        visibility: ``"shared"`` to target the default/sole group;
            ``"personal"`` or omitted for the personal space.

    Returns:
        The writable target space.

    Raises:
        ForbiddenError: Unauthenticated caller.
        NotFoundError: Inaccessible ``space_id``.
        SpaceResolveError: Group name or shared default cannot be resolved to one group.
    """
    if space_id is not None:
        space = await get_readable_space(session, principal, space_id)
        if not space:
            raise NotFoundError("Space not found or not accessible")
        return space
    if group and group.strip():
        return await resolve_group_by_name(session, principal, group)
    if visibility == "shared":
        return await _resolve_shared_default(session, principal)
    space = await get_personal_space(session, principal.user_id)
    if not space:
        user = await session.get(User, principal.user_id)
        if not user:
            raise ForbiddenError("User not found")
        space = await ensure_personal_space(session, user)
        await commit_with_rls(session, user_id=principal.user_id, is_admin=principal.is_admin)
    return space


async def set_default_shared_space(
    session: AsyncSession, principal: Principal, space_id: uuid.UUID | None
) -> User:
    """Set or clear the caller's default shared group.

    Args:
        session: Database session.
        principal: Authenticated user principal.
        space_id: Group to use for ``visibility="shared"``; None clears it.

    Returns:
        The updated user.

    Raises:
        PermissionError: Missing user principal, or the group is not one the
            caller belongs to.
    """
    user = await session.get(User, principal.user_id)
    if not user:
        raise ForbiddenError("User not found")
    if space_id is not None:
        groups = await _member_groups(session, principal)
        if space_id not in {g.id for g in groups}:
            raise NotFoundError("Default shared group must be an active group you belong to")
    user.default_shared_space_id = space_id
    await commit_with_rls(session, user_id=principal.user_id, is_admin=principal.is_admin)
    await session.refresh(user)
    return user
