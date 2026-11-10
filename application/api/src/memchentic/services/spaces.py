from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from memchentic.auth.principal import Principal
from memchentic.db.session import commit_with_rls, set_rls_context
from memchentic.models.entities import Space, SpaceKind, SpaceMember, User
from memchentic.services import limits
from memchentic.services.errors import ForbiddenError, NotFoundError, ValidationError
from memchentic.services.validation import check_name


async def ensure_personal_space(session: AsyncSession, user: User) -> Space:
    # FORCE RLS: SELECT/INSERT policies key off app.user_id — always bind it
    # to the space owner before looking up or creating their personal space.
    await set_rls_context(session, user_id=user.id, is_admin=False)
    result = await session.execute(
        select(Space).where(Space.kind == SpaceKind.personal, Space.created_by_user_id == user.id)
    )
    space = result.scalar_one_or_none()
    if space:
        return space
    space_id = uuid.uuid4()
    # SECURITY DEFINER helper evaluates WITH CHECK under the caller's GUC and
    # inserts the membership row in the same privileged statement (avoids the
    # INSERT…RETURNING + SELECT-policy race under FORCE RLS).
    await session.execute(
        text("SELECT app_insert_space(:id, CAST(:kind AS space_kind), :name, :created_by)"),
        {
            "id": space_id,
            "kind": SpaceKind.personal.value,
            "name": "Personal",
            "created_by": user.id,
        },
    )
    await session.flush()
    space = await session.get(Space, space_id)
    if space is None:
        # Concurrent create or prior partial insert: re-read under RLS.
        result = await session.execute(
            select(Space).where(
                Space.kind == SpaceKind.personal, Space.created_by_user_id == user.id
            )
        )
        space = result.scalar_one_or_none()
    if space is None:
        raise RuntimeError("personal space insert failed")
    return space


async def get_personal_space(session: AsyncSession, user_id: uuid.UUID) -> Space | None:
    result = await session.execute(
        select(Space).where(Space.kind == SpaceKind.personal, Space.created_by_user_id == user_id)
    )
    return result.scalar_one_or_none()


async def list_member_spaces(session: AsyncSession, principal: Principal) -> list[Space]:
    """Spaces the caller is a member of. Admin status does not widen the list.

    Use this for "my spaces" (the ``memberships`` field of ``me``). Use
    ``list_spaces`` for "spaces I may read".

    Args:
        session: Database session.
        principal: Authenticated caller.

    Returns:
        The caller's personal space and groups, unarchived.
    """
    if not principal.user_id:
        return []
    result = await session.execute(
        select(Space)
        .join(SpaceMember, SpaceMember.space_id == Space.id)
        .where(
            SpaceMember.user_id == principal.user_id,
            SpaceMember.accepted_at.is_not(None),
            Space.archived_at.is_(None),
        )
        .order_by(Space.kind.asc(), Space.name.asc())
    )
    return list(result.scalars())


async def list_spaces(session: AsyncSession, principal: Principal) -> list[Space]:
    """Spaces the caller may read: every space for an admin, else memberships."""
    if principal.is_admin:
        result = await session.execute(
            select(Space).where(Space.archived_at.is_(None)).order_by(Space.created_at.desc())
        )
        return list(result.scalars())
    return await list_member_spaces(session, principal)


async def create_group(session: AsyncSession, principal: Principal, *, name: str) -> Space:
    if not principal.user_id:
        raise ForbiddenError("Authentication required")
    name = check_name(name, "name", limits.SPACE_NAME_MAX)
    owner_id = principal.user_id
    space_id = uuid.uuid4()
    await session.execute(
        text("SELECT app_insert_space(:id, CAST(:kind AS space_kind), :name, :created_by)"),
        {
            "id": space_id,
            "kind": SpaceKind.group.value,
            "name": name.strip(),
            "created_by": owner_id,
        },
    )
    # Load before commit: commit/check-in clears RLS GUCs and would hide the row.
    space = await session.get(Space, space_id)
    if space is None:
        raise RuntimeError("group space insert failed")
    await commit_with_rls(session, user_id=principal.user_id, is_admin=principal.is_admin)
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
    member_ids = {m.user_id for m in space.members if m.accepted_at is not None}
    if principal.user_id not in member_ids:
        return None
    return space


def visible_members(space: Space, principal: Principal) -> list[SpaceMember]:
    """Roster rows the caller may see.

    Accepted members are visible to everyone who can open the space. Pending
    invites are visible to admins only: showing them to the group creator
    would reveal whether an invited email address is registered.

    Args:
        space: Space loaded by ``show_space`` (members eagerly loaded).
        principal: Authenticated caller.

    Returns:
        Membership rows to serialise.
    """
    if principal.is_admin:
        return list(space.members)
    return [m for m in space.members if m.accepted_at is not None]


def space_to_dict(space: Space) -> dict[str, str]:
    """Serialise a space to the short form used in listings and memberships.

    Args:
        space: The space row.

    Returns:
        ``id``, ``name`` and ``kind`` of the space.
    """
    return {"id": str(space.id), "name": space.name, "kind": space.kind.value}


def space_detail(space: Space, principal: Principal) -> dict[str, Any]:
    """Serialise a space with its creator and the roster the caller may see.

    Args:
        space: The space row with ``members`` loaded.
        principal: Authenticated caller. ``visible_members`` uses it to hide
            pending invites from everyone except admins.

    Returns:
        The short form plus ``created_by`` and ``members``.
    """
    return {
        **space_to_dict(space),
        "created_by": str(space.created_by_user_id) if space.created_by_user_id else None,
        "members": [
            {
                "user_id": str(m.user_id),
                "email": m.user.email if m.user else None,
                "display_name": m.user.display_name if m.user else None,
                "pending": m.accepted_at is None,
            }
            for m in visible_members(space, principal)
        ],
    }


async def me_payload(
    session: AsyncSession, principal: Principal, *, invite_details: bool
) -> dict[str, Any]:
    """Build the identity payload returned by ``GET /api/me`` and the ``me`` tool.

    Args:
        session: Database session with the caller's RLS context.
        principal: Authenticated caller.
        invite_details: True for REST: the payload carries ``pending_invites``
            (group id, group name, creator labels). False for MCP: it carries
            only ``pending_invite_count``, because a group name in a pending
            invite is text chosen by another user.

    Returns:
        Identity fields, the personal space id, the default shared group id,
        the caller's memberships, and the pending invites (list or count).
    """
    spaces = await list_member_spaces(session, principal)
    personal = await get_personal_space(session, principal.user_id) if principal.user_id else None
    user = await session.get(User, principal.user_id) if principal.user_id else None
    default_shared = user.default_shared_space_id if user else None
    invites = await list_pending_invites(session, principal)
    payload: dict[str, Any] = {
        "default_shared_space_id": str(default_shared) if default_shared else None,
        "id": str(principal.user_id) if principal.user_id else None,
        "subject": principal.subject,
        "email": principal.email,
        "display_name": principal.display_name,
        "provider": principal.provider,
        "is_admin": principal.is_admin,
        "personal_space_id": str(personal.id) if personal else None,
        "memberships": [
            {
                **space_to_dict(s),
                "created_by": str(s.created_by_user_id) if s.created_by_user_id else None,
            }
            for s in spaces
        ],
    }
    if invite_details:
        payload["pending_invites"] = invites
    else:
        payload["pending_invite_count"] = len(invites)
    return payload


async def get_readable_space(
    session: AsyncSession, principal: Principal, space_id: uuid.UUID
) -> Space | None:
    """Return the space when the principal may read it, without its roster.

    Same access rule as ``show_space``: administrators may read every space,
    other callers must be an accepted member (a pending invite grants
    nothing). ``Space.members`` is not loaded; use ``show_space`` when the
    roster is needed.

    Args:
        session: Database session.
        principal: Authenticated caller.
        space_id: Space id.

    Returns:
        The space, or None when it does not exist or is not readable.
    """
    stmt = select(Space).where(Space.id == space_id)
    if not principal.is_admin:
        if not principal.user_id:
            return None
        stmt = stmt.join(SpaceMember, SpaceMember.space_id == Space.id).where(
            SpaceMember.user_id == principal.user_id,
            SpaceMember.accepted_at.is_not(None),
        )
    return (await session.execute(stmt)).scalar_one_or_none()


async def list_visible_groups_for_user(
    session: AsyncSession, principal: Principal, target_user_id: uuid.UUID
) -> list[Space]:
    """List unarchived groups that include ``target_user_id`` and the caller may see.

    Admins see every group the target belongs to. Other principals only see groups
    where both they and the target are members. Personal spaces are excluded.

    Args:
        session: Database session.
        principal: Authenticated caller (never a client-supplied identity).
        target_user_id: Directory user whose memberships to summarize.

    Returns:
        Group spaces ordered by name.
    """
    if principal.is_admin:
        result = await session.execute(
            select(Space)
            .join(SpaceMember, SpaceMember.space_id == Space.id)
            .where(
                SpaceMember.user_id == target_user_id,
                SpaceMember.accepted_at.is_not(None),
                Space.kind == SpaceKind.group,
                Space.archived_at.is_(None),
            )
            .order_by(Space.name.asc())
        )
        return list(result.scalars().unique())
    if not principal.user_id:
        return []
    target_m = aliased(SpaceMember)
    caller_m = aliased(SpaceMember)
    result = await session.execute(
        select(Space)
        .join(target_m, target_m.space_id == Space.id)
        .join(caller_m, caller_m.space_id == Space.id)
        .where(
            target_m.user_id == target_user_id,
            target_m.accepted_at.is_not(None),
            caller_m.user_id == principal.user_id,
            caller_m.accepted_at.is_not(None),
            Space.kind == SpaceKind.group,
            Space.archived_at.is_(None),
        )
        .order_by(Space.name.asc())
    )
    return list(result.scalars().unique())


def can_manage_group(space: Space, principal: Principal) -> bool:
    if principal.is_admin:
        return True
    return bool(principal.user_id and space.created_by_user_id == principal.user_id)


async def require_roster_manager(
    session: AsyncSession, principal: Principal, space_id: uuid.UUID
) -> Space:
    """Return the group only when the principal may manage its roster.

    Args:
        session: Database session.
        principal: Authenticated caller.
        space_id: Group space id.

    Returns:
        The group space.

    Raises:
        NotFoundError: When the group is not visible.
        ForbiddenError: When the caller is not creator/admin.
    """
    space = await show_space(session, principal, space_id)
    if not space or space.kind != SpaceKind.group:
        raise NotFoundError("Group not found")
    if not can_manage_group(space, principal):
        raise ForbiddenError("Only the group creator or admin can manage membership")
    return space


async def add_member(
    session: AsyncSession,
    principal: Principal,
    space_id: uuid.UUID,
    user_id: uuid.UUID,
) -> SpaceMember:
    """Invite a user to a group the principal manages; idempotent.

    The row starts pending (``accepted_at`` NULL) and grants nothing until
    the invited user calls ``accept_invite``. A caller who adds themselves is
    accepted at once.

    Args:
        session: Database session.
        principal: Authenticated caller.
        space_id: Group space id.
        user_id: User to invite.

    Returns:
        The membership row (pending or accepted).

    Raises:
        NotFoundError: When no user has ``user_id``.
    """
    await require_roster_manager(session, principal, space_id)
    if await session.get(User, user_id) is None:
        raise NotFoundError("User not found")
    # One statement instead of select-then-insert: an overlapping identical
    # add waits on the primary key and is then skipped, not raised.
    await session.execute(
        pg_insert(SpaceMember)
        .values(
            space_id=space_id,
            user_id=user_id,
            accepted_at=datetime.now(UTC) if user_id == principal.user_id else None,
        )
        .on_conflict_do_nothing(index_elements=["space_id", "user_id"])
    )
    result = await session.execute(
        select(SpaceMember).where(SpaceMember.space_id == space_id, SpaceMember.user_id == user_id)
    )
    member = result.scalar_one()
    await commit_with_rls(session, user_id=principal.user_id, is_admin=principal.is_admin)
    return member


async def accept_invite(session: AsyncSession, principal: Principal, space_id: uuid.UUID) -> Space:
    """Accept the caller's pending invite to a group; idempotent.

    Args:
        session: Database session.
        principal: Authenticated caller (the invited user).
        space_id: Group space id.

    Returns:
        The group, now readable by the caller.

    Raises:
        ForbiddenError: The caller is not authenticated.
        NotFoundError: The caller has no invite or membership in that group.
    """
    if not principal.user_id:
        raise ForbiddenError("Authentication required")
    result = await session.execute(
        select(SpaceMember).where(
            SpaceMember.space_id == space_id,
            SpaceMember.user_id == principal.user_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise NotFoundError("Invite not found")
    if member.accepted_at is None:
        member.accepted_at = datetime.now(UTC)
        await commit_with_rls(session, user_id=principal.user_id, is_admin=principal.is_admin)
    space = await show_space(session, principal, space_id)
    if space is None or space.kind != SpaceKind.group:
        raise NotFoundError("Invite not found")
    return space


async def list_pending_invites(session: AsyncSession, principal: Principal) -> list[dict[str, Any]]:
    """Group invites the caller has not accepted yet.

    The group name is text chosen by the inviting user. Show it to a human
    (REST, GUI). Do not return it to an agent: the MCP ``me`` tool reports a
    count only.

    Args:
        session: Database session.
        principal: Authenticated caller.

    Returns:
        One dict per pending invite: group id, group name, creator labels.
    """
    if not principal.user_id:
        return []
    creator = aliased(User)
    result = await session.execute(
        select(Space, creator)
        .join(SpaceMember, SpaceMember.space_id == Space.id)
        .outerjoin(creator, creator.id == Space.created_by_user_id)
        .where(
            SpaceMember.user_id == principal.user_id,
            SpaceMember.accepted_at.is_(None),
            Space.kind == SpaceKind.group,
            Space.archived_at.is_(None),
        )
        .order_by(Space.name.asc())
    )
    return [
        {
            "id": str(space.id),
            "name": space.name,
            "creator_email": owner.email if owner else None,
            "creator_display_name": owner.display_name if owner else None,
        }
        for space, owner in result.all()
    ]


async def remove_member(
    session: AsyncSession,
    principal: Principal,
    space_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    """Remove a member, leave a group, or decline a pending invite.

    Args:
        session: Database session.
        principal: Authenticated caller.
        space_id: Group space id.
        user_id: Member to remove; the caller's own id leaves or declines.

    Raises:
        NotFoundError: Group missing or not visible to the caller.
        ForbiddenError: Caller may not manage the roster, or the target is
            the group creator.
    """
    leaving_self = principal.user_id is not None and principal.user_id == user_id
    space = await show_space(session, principal, space_id)
    if space is None and leaving_self:
        # Not a member yet: the caller may still delete their own pending row.
        pending = (
            await session.execute(
                select(SpaceMember).where(
                    SpaceMember.space_id == space_id,
                    SpaceMember.user_id == user_id,
                    SpaceMember.accepted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if pending is None:
            raise NotFoundError("Group not found")
        await session.delete(pending)
        await commit_with_rls(session, user_id=principal.user_id, is_admin=principal.is_admin)
        return
    if not space or space.kind != SpaceKind.group:
        raise NotFoundError("Group not found")
    if not leaving_self and not can_manage_group(space, principal):
        raise ForbiddenError("Only the group creator or admin can manage membership")
    if space.created_by_user_id == user_id:
        raise ForbiddenError("Cannot remove the group creator")
    result = await session.execute(
        select(SpaceMember).where(SpaceMember.space_id == space_id, SpaceMember.user_id == user_id)
    )
    member = result.scalar_one_or_none()
    if member:
        await session.delete(member)
        await commit_with_rls(session, user_id=principal.user_id, is_admin=principal.is_admin)


async def rename_space(
    session: AsyncSession, principal: Principal, space_id: uuid.UUID, name: str
) -> Space:
    name = check_name(name, "name", limits.SPACE_NAME_MAX)
    space = await show_space(session, principal, space_id)
    if not space:
        raise NotFoundError("Space not found")
    if space.kind == SpaceKind.group and not can_manage_group(space, principal):
        raise ForbiddenError("Only the group creator or admin can rename")
    if space.kind == SpaceKind.personal and not (
        principal.is_admin or space.created_by_user_id == principal.user_id
    ):
        raise ForbiddenError("Cannot rename another user's personal space")
    space.name = name.strip()
    await commit_with_rls(session, user_id=principal.user_id, is_admin=principal.is_admin)
    await session.refresh(space)
    return space


def can_delete_group(space: Space, principal: Principal) -> bool:
    """True when the caller owns the group (or is break-glass admin)."""
    if space.kind != SpaceKind.group:
        return False
    if principal.user_id and space.created_by_user_id == principal.user_id:
        return True
    return bool(principal.is_admin)


async def delete_space(session: AsyncSession, principal: Principal, space_id: uuid.UUID) -> None:
    """Hard-delete a group the principal owns (memories and members cascade).

    Args:
        session: Database session.
        principal: Authenticated caller.
        space_id: Group space id.

    Raises:
        NotFoundError: Missing group.
        ForbiddenError: Caller is not the owner (or admin).
    """
    space = await show_space(session, principal, space_id)
    if not space or space.kind != SpaceKind.group:
        raise NotFoundError("Group not found")
    if not can_delete_group(space, principal):
        raise ForbiddenError("Only the group owner can delete")
    await session.delete(space)
    await commit_with_rls(session, user_id=principal.user_id, is_admin=principal.is_admin)


Visibility = Literal["personal", "shared"]


class SpaceResolveError(ValidationError):
    """A write target (group name or shared default) did not resolve to one group."""


def _group_choices(groups: list[Space]) -> str:
    return ", ".join(f"{g.name} ({g.id})" for g in groups) or "none"


async def _member_groups(session: AsyncSession, principal: Principal) -> list[Space]:
    """Groups the caller belongs to (membership join — not admin instance-wide list)."""
    if not principal.user_id:
        return []
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
    if not principal.user_id:
        raise ForbiddenError("Authentication required")
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
    if not principal.user_id:
        raise ForbiddenError("Default shared group needs a user principal")
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
