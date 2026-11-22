"""Projects: named work areas inside a space (not an ACL boundary)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import Principal
from memchentic.db.session import commit_with_rls
from memchentic.models.entities import Project, Space, SpaceKind
from memchentic.services import limits
from memchentic.services.errors import ConflictError, ForbiddenError, NotFoundError
from memchentic.services.spaces import (
    get_readable_space,
    is_group_admin,
    show_space,
)
from memchentic.services.validation import check_name, require_uuid


def project_to_dict(project: Project) -> dict[str, Any]:
    """Serialise a project for REST and MCP."""
    return {
        "id": str(project.id),
        "space_id": str(project.space_id),
        "name": project.name,
        "created_by": str(project.created_by_user_id) if project.created_by_user_id else None,
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }


def can_manage_project(project: Project, space: Space, principal: Principal) -> bool:
    """Return True when the caller may rename or delete ``project``."""
    if principal.is_admin:
        return True
    if project.created_by_user_id and project.created_by_user_id == principal.user_id:
        return True
    if space.kind == SpaceKind.personal:
        return space.created_by_user_id == principal.user_id
    return is_group_admin(space, principal)


async def list_projects(
    session: AsyncSession,
    principal: Principal,
    *,
    space_id: uuid.UUID | None = None,
) -> list[Project]:
    """List projects in spaces the caller can read.

    Args:
        session: Database session.
        principal: Authenticated caller.
        space_id: Optional single-space filter.

    Returns:
        Projects ordered by name.
    """
    if space_id is not None:
        space = await get_readable_space(session, principal, space_id)
        if space is None:
            raise NotFoundError("Space not found")
        result = await session.execute(
            select(Project).where(Project.space_id == space_id).order_by(Project.name.asc())
        )
        return list(result.scalars().all())

    from memchentic.services.spaces import accessible_space_ids

    space_ids = await accessible_space_ids(session, principal, None)
    if not space_ids:
        return []
    result = await session.execute(
        select(Project).where(Project.space_id.in_(space_ids)).order_by(Project.name.asc())
    )
    return list(result.scalars().all())


async def get_project(
    session: AsyncSession, principal: Principal, project_id: uuid.UUID
) -> Project | None:
    """Return a project when the caller can read its space."""
    result = await session.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None:
        return None
    space = await get_readable_space(session, principal, project.space_id)
    if space is None:
        return None
    return project


async def create_project(
    session: AsyncSession,
    principal: Principal,
    *,
    space_id: uuid.UUID,
    name: str,
) -> Project:
    """Create a project in a readable space.

    Raises:
        NotFoundError: Space not readable.
        ConflictError: Duplicate name in the space.
        ValidationError: Invalid name.
    """
    name = check_name(name, "name", limits.SPACE_NAME_MAX)
    space = await get_readable_space(session, principal, space_id)
    if space is None:
        raise NotFoundError("Space not found")
    existing = await session.execute(
        select(Project.id).where(
            Project.space_id == space_id,
            func.lower(func.btrim(Project.name)) == name.strip().lower(),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError("A project with that name already exists in this space")
    project = Project(
        id=uuid.uuid4(),
        space_id=space_id,
        name=name.strip(),
        created_by_user_id=principal.user_id,
    )
    session.add(project)
    await commit_with_rls(session, user_id=principal.user_id, is_admin=principal.is_admin)
    await session.refresh(project)
    return project


async def rename_project(
    session: AsyncSession,
    principal: Principal,
    project_id: uuid.UUID,
    name: str,
) -> Project:
    """Rename a project the caller may manage."""
    name = check_name(name, "name", limits.SPACE_NAME_MAX)
    project = await get_project(session, principal, project_id)
    if project is None:
        raise NotFoundError("Project not found")
    space = await show_space(session, principal, project.space_id)
    if space is None or not can_manage_project(project, space, principal):
        raise ForbiddenError("Only the project creator or a space manager can rename")
    clash = await session.execute(
        select(Project.id).where(
            Project.space_id == project.space_id,
            Project.id != project.id,
            func.lower(func.btrim(Project.name)) == name.strip().lower(),
        )
    )
    if clash.scalar_one_or_none() is not None:
        raise ConflictError("A project with that name already exists in this space")
    project.name = name.strip()
    project.updated_at = datetime.now(UTC)
    await commit_with_rls(session, user_id=principal.user_id, is_admin=principal.is_admin)
    await session.refresh(project)
    return project


async def delete_project(
    session: AsyncSession, principal: Principal, project_id: uuid.UUID
) -> None:
    """Delete a project; memories keep their rows with ``project_id`` cleared."""
    project = await get_project(session, principal, project_id)
    if project is None:
        raise NotFoundError("Project not found")
    space = await show_space(session, principal, project.space_id)
    if space is None or not can_manage_project(project, space, principal):
        raise ForbiddenError("Only the project creator or a space manager can delete")
    await session.delete(project)
    await commit_with_rls(session, user_id=principal.user_id, is_admin=principal.is_admin)


async def require_project_in_space(
    session: AsyncSession,
    principal: Principal,
    project_id: uuid.UUID | None,
    space_id: uuid.UUID,
) -> uuid.UUID | None:
    """Validate an optional project id belongs to ``space_id`` and is readable.

    Returns:
        The project id, or None when ``project_id`` is None.

    Raises:
        NotFoundError: Unknown or cross-space project.
    """
    if project_id is None:
        return None
    project = await get_project(session, principal, project_id)
    if project is None or project.space_id != space_id:
        raise NotFoundError("Project not found")
    return project.id


async def resolve_project_id(
    session: AsyncSession,
    principal: Principal,
    *,
    project_id: str | uuid.UUID | None,
    space_id: uuid.UUID,
) -> uuid.UUID | None:
    """Parse and validate an optional project id for a write or filter."""
    if project_id is None or project_id == "":
        return None
    pid = (
        project_id
        if isinstance(project_id, uuid.UUID)
        else require_uuid(str(project_id), "project_id")
    )
    return await require_project_in_space(session, principal, pid, space_id)
