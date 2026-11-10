"""Space CRUD, membership and invites."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.api.schemas import GroupIn, MemberIn
from memchentic.auth.principal import Principal
from memchentic.auth.users import get_user, get_user_by_email
from memchentic.deps import db_session, get_principal
from memchentic.models.entities import User
from memchentic.services import spaces as space_service
from memchentic.services.errors import ValidationError
from memchentic.services.validation import require_uuid

router = APIRouter()


@router.get("/spaces")
async def spaces(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> list[dict]:
    """List the spaces the caller can read. Authenticated callers."""
    rows = await space_service.list_spaces(session, principal)
    return [space_service.space_to_dict(s) for s in rows]


@router.post("/spaces")
async def create_space(
    body: GroupIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Create a group owned by the caller. Authenticated users."""
    space = await space_service.create_group(session, principal, name=body.name)
    return space_service.space_to_dict(space)


@router.get("/spaces/{space_id}")
async def show_space(
    space_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Return one space with its members. Callers who can read the space.

    Errors: 404 when the space does not exist or is not readable.
    """
    space = await space_service.show_space(session, principal, space_id)
    if not space:
        raise HTTPException(404, "Not found")
    return space_service.space_detail(space, principal)


@router.patch("/spaces/{space_id}")
async def rename_space(
    space_id: uuid.UUID,
    body: GroupIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Rename a space. The group creator, the personal-space owner, or an administrator.

    Errors: 403 when not allowed, 404 when the space does not exist.
    """
    space = await space_service.rename_space(session, principal, space_id, body.name)
    return space_service.space_to_dict(space)


@router.delete("/spaces/{space_id}")
async def delete_space(
    space_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Delete a group. The group creator or an administrator.

    Errors: 403 when not allowed, 404 when the space does not exist.
    """
    await space_service.delete_space(session, principal, space_id)
    return {"status": "ok"}


@router.post("/spaces/{space_id}/members")
async def add_member(
    space_id: uuid.UUID,
    body: MemberIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Invite a user to a group by id or by email.

    The response is identical whether or not the target exists, so a group
    creator cannot use this route to test which addresses are registered.
    """
    await space_service.require_roster_manager(session, principal, space_id)
    target: User | None
    if body.user_id:
        target = await get_user(session, require_uuid(body.user_id, "user_id"))
    elif body.email and body.email.strip():
        target = await get_user_by_email(session, body.email)
    else:
        raise ValidationError("user_id or email required")
    if target is not None:
        await space_service.add_member(session, principal, space_id, target.id)
    return {"status": "ok"}


@router.post("/spaces/{space_id}/accept")
async def accept_space_invite(
    space_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Accept the caller's pending invite to a group (idempotent).

    A missing invite returns 404 so the route does not confirm that a group
    id exists.
    """
    space = await space_service.accept_invite(session, principal, space_id)
    return space_service.space_to_dict(space)


@router.delete("/spaces/{space_id}/members/{user_id}")
async def remove_member(
    space_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Remove a member from a group.

    Errors: 403 when not allowed, 404 when the space or member does not exist.
    """
    await space_service.remove_member(session, principal, space_id, user_id)
    return {"status": "ok"}
