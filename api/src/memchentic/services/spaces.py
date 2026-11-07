from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from memchentic.auth.principal import Principal
from memchentic.models.entities import Space, SpaceKind, SpaceMember, User


async def ensure_personal_space(session: AsyncSession, user: User) -> Space:
    result = await session.execute(
        select(Space).where(Space.kind == SpaceKind.personal, Space.created_by_user_id == user.id)
    )
    space = result.scalar_one_or_none()
    if space:
        return space
    space = Space(
        id=uuid.uuid4(),
        kind=SpaceKind.personal,
        name="Personal",
        created_by_user_id=user.id,
    )
    session.add(space)
    await session.flush()
    session.add(SpaceMember(space_id=space.id, user_id=user.id))
    await session.flush()
    return space


async def get_personal_space(session: AsyncSession, user_id: uuid.UUID) -> Space | None:
    result = await session.execute(
        select(Space).where(Space.kind == SpaceKind.personal, Space.created_by_user_id == user_id)
    )
    return result.scalar_one_or_none()


async def list_spaces(session: AsyncSession, principal: Principal) -> list[Space]:
    if principal.is_admin and principal.user_id is None:
        result = await session.execute(
            select(Space).where(Space.archived_at.is_(None)).order_by(Space.created_at.desc())
        )
        return list(result.scalars())
    if not principal.user_id:
        return []
    result = await session.execute(
        select(Space)
        .join(SpaceMember, SpaceMember.space_id == Space.id)
        .where(SpaceMember.user_id == principal.user_id, Space.archived_at.is_(None))
        .order_by(Space.kind.asc(), Space.name.asc())
    )
    return list(result.scalars())


async def create_team(
    session: AsyncSession, principal: Principal, *, name: str
) -> Space:
    if not principal.user_id and not principal.is_admin:
        raise PermissionError("Authentication required")
    owner_id = principal.user_id
    if principal.is_admin and owner_id is None:
        raise PermissionError("Admin API key cannot own teams; use a user account")
    space = Space(
        id=uuid.uuid4(),
        kind=SpaceKind.team,
        name=name.strip(),
        created_by_user_id=owner_id,
    )
    session.add(space)
    await session.flush()
    session.add(SpaceMember(space_id=space.id, user_id=owner_id))  # type: ignore[arg-type]
    await session.commit()
    await session.refresh(space)
    return space


async def show_space(
    session: AsyncSession, principal: Principal, space_id: uuid.UUID
) -> Space | None:
    result = await session.execute(
        select(Space)
        .options(selectinload(Space.members).selectinload(SpaceMember.user))
        .where(Space.id == space_id)
    )
    space = result.scalar_one_or_none()
    if not space:
        return None
    if principal.is_admin:
        return space
    if not principal.user_id:
        return None
    member_ids = {m.user_id for m in space.members}
    if principal.user_id not in member_ids:
        return None
    return space


def can_manage_team(space: Space, principal: Principal) -> bool:
    if principal.is_admin:
        return True
    return bool(principal.user_id and space.created_by_user_id == principal.user_id)


async def require_roster_manager(
    session: AsyncSession, principal: Principal, space_id: uuid.UUID
) -> Space:
    """Return the team only when the principal may manage its roster.

    Args:
        session: Database session.
        principal: Authenticated caller.
        space_id: Team space id.

    Returns:
        The team space.

    Raises:
        PermissionError: When the team is not visible or the caller is not creator/admin.
    """
    space = await show_space(session, principal, space_id)
    if not space or space.kind != SpaceKind.team:
        raise PermissionError("Team not found")
    if not can_manage_team(space, principal):
        raise PermissionError("Only the team creator or admin can manage membership")
    return space


async def add_member(
    session: AsyncSession,
    principal: Principal,
    space_id: uuid.UUID,
    user_id: uuid.UUID,
) -> SpaceMember:
    """Add a user to a team the principal manages; idempotent.

    Args:
        session: Database session.
        principal: Authenticated caller.
        space_id: Team space id.
        user_id: User to add.

    Returns:
        The membership row.
    """
    await require_roster_manager(session, principal, space_id)
    existing = await session.execute(
        select(SpaceMember).where(SpaceMember.space_id == space_id, SpaceMember.user_id == user_id)
    )
    member = existing.scalar_one_or_none()
    if member:
        return member
    member = SpaceMember(space_id=space_id, user_id=user_id)
    session.add(member)
    await session.commit()
    return member


async def remove_member(
    session: AsyncSession,
    principal: Principal,
    space_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    space = await show_space(session, principal, space_id)
    if not space or space.kind != SpaceKind.team:
        raise PermissionError("Team not found")
    leaving_self = principal.user_id is not None and principal.user_id == user_id
    if not leaving_self and not can_manage_team(space, principal):
        raise PermissionError("Only the team creator or admin can manage membership")
    if space.created_by_user_id == user_id:
        raise ValueError("Cannot remove the team creator")
    result = await session.execute(
        select(SpaceMember).where(SpaceMember.space_id == space_id, SpaceMember.user_id == user_id)
    )
    member = result.scalar_one_or_none()
    if member:
        await session.delete(member)
        await session.commit()


async def rename_space(
    session: AsyncSession, principal: Principal, space_id: uuid.UUID, name: str
) -> Space:
    space = await show_space(session, principal, space_id)
    if not space:
        raise PermissionError("Space not found")
    if space.kind == SpaceKind.team and not can_manage_team(space, principal):
        raise PermissionError("Only the team creator or admin can rename")
    if space.kind == SpaceKind.personal and not (
        principal.is_admin or space.created_by_user_id == principal.user_id
    ):
        raise PermissionError("Cannot rename another user's personal space")
    space.name = name.strip()
    await session.commit()
    await session.refresh(space)
    return space


async def archive_space(
    session: AsyncSession, principal: Principal, space_id: uuid.UUID
) -> Space:
    space = await show_space(session, principal, space_id)
    if not space or space.kind != SpaceKind.team:
        raise PermissionError("Team not found")
    if not can_manage_team(space, principal):
        raise PermissionError("Only the team creator or admin can archive")
    space.archived_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(space)
    return space


Visibility = Literal["personal", "shared"]


class SpaceResolveError(ValueError):
    """A write target (team name or shared default) did not resolve to one team."""


def _team_choices(teams: list[Space]) -> str:
    return ", ".join(f"{t.name} ({t.id})" for t in teams) or "none"


async def _member_teams(session: AsyncSession, principal: Principal) -> list[Space]:
    return [s for s in await list_spaces(session, principal) if s.kind == SpaceKind.team]


async def resolve_team_by_name(
    session: AsyncSession, principal: Principal, team: str
) -> Space:
    """Resolve a team name to exactly one unarchived team the caller belongs to.

    Args:
        session: Database session.
        principal: Authenticated user principal.
        team: Team name; matched case-insensitively and exactly.

    Returns:
        The single matching team.

    Raises:
        ValueError: When zero or several of the caller's teams match.
    """
    teams = await _member_teams(session, principal)
    wanted = team.strip().casefold()
    matches = [t for t in teams if t.name.strip().casefold() == wanted]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SpaceResolveError(f"No team named {team!r} among yours; teams: {_team_choices(teams)}")
    raise SpaceResolveError(
        f"Team name {team!r} is ambiguous; pass space_id. Matches: {_team_choices(matches)}"
    )


async def _resolve_shared_default(
    session: AsyncSession, principal: Principal
) -> Space:
    teams = await _member_teams(session, principal)
    user = await session.get(User, principal.user_id)
    default_id = user.default_shared_space_id if user else None
    for team in teams:
        if team.id == default_id:
            return team
    if len(teams) == 1:
        return teams[0]
    if not teams:
        raise SpaceResolveError("visibility=shared but you are not on any team")
    raise SpaceResolveError(
        "visibility=shared needs team, space_id, or a default shared team; "
        f"teams: {_team_choices(teams)}"
    )


async def resolve_write_space(
    session: AsyncSession,
    principal: Principal,
    space_id: uuid.UUID | None,
    *,
    team: str | None = None,
    visibility: Visibility | None = None,
) -> Space:
    """Pick the space a write lands in.

    Precedence: explicit ``space_id``, then ``team`` name, then
    ``visibility="shared"`` (default shared team, else the sole team), else
    the caller's personal space.

    Args:
        session: Database session.
        principal: Authenticated caller.
        space_id: Explicit target; skips name and default resolution.
        team: Team name to resolve among the caller's memberships.
        visibility: ``"shared"`` to target the default/sole team;
            ``"personal"`` or omitted for the personal space.

    Returns:
        The writable target space.

    Raises:
        PermissionError: Unauthenticated, inaccessible space, or admin key
            without ``space_id``.
        ValueError: Team name or shared default cannot be resolved to one team.
    """
    if not principal.user_id and not principal.is_admin:
        raise PermissionError("Authentication required")
    if space_id is not None:
        space = await show_space(session, principal, space_id)
        if not space:
            raise PermissionError("Space not found or not accessible")
        return space
    if not principal.user_id:
        raise PermissionError("Admin API key must pass space_id")
    if team and team.strip():
        return await resolve_team_by_name(session, principal, team)
    if visibility == "shared":
        return await _resolve_shared_default(session, principal)
    space = await get_personal_space(session, principal.user_id)
    if not space:
        user = await session.get(User, principal.user_id)
        if not user:
            raise PermissionError("User not found")
        space = await ensure_personal_space(session, user)
        await session.commit()
    return space


async def set_default_shared_space(
    session: AsyncSession, principal: Principal, space_id: uuid.UUID | None
) -> User:
    """Set or clear the caller's default shared team.

    Args:
        session: Database session.
        principal: Authenticated user principal.
        space_id: Team to use for ``visibility="shared"``; None clears it.

    Returns:
        The updated user.

    Raises:
        PermissionError: Admin API key, or the team is not one the caller belongs to.
    """
    if not principal.user_id:
        raise PermissionError("Default shared team needs a user principal")
    user = await session.get(User, principal.user_id)
    if not user:
        raise PermissionError("User not found")
    if space_id is not None:
        teams = await _member_teams(session, principal)
        if space_id not in {t.id for t in teams}:
            raise PermissionError("Default shared team must be an active team you belong to")
    user.default_shared_space_id = space_id
    await session.commit()
    await session.refresh(user)
    return user
