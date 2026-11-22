"""Project CRUD under spaces."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.api.schemas import ProjectIn, ProjectPatch
from memchentic.auth.principal import Principal
from memchentic.deps import db_session, get_principal
from memchentic.services import projects as project_service
from memchentic.services.validation import require_uuid

router = APIRouter()


@router.get("/projects")
async def list_projects(
    space_id: uuid.UUID | None = None,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> list[dict]:
    """List projects in spaces the caller can read."""
    rows = await project_service.list_projects(session, principal, space_id=space_id)
    return [project_service.project_to_dict(p) for p in rows]


@router.post("/projects")
async def create_project(
    body: ProjectIn,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Create a project in a readable space."""
    project = await project_service.create_project(
        session,
        principal,
        space_id=require_uuid(body.space_id, "space_id"),
        name=body.name,
    )
    return project_service.project_to_dict(project)


@router.get("/projects/{project_id}")
async def show_project(
    project_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Return one project the caller can read."""
    from fastapi import HTTPException

    project = await project_service.get_project(session, principal, project_id)
    if project is None:
        raise HTTPException(404, "Not found")
    return project_service.project_to_dict(project)


@router.patch("/projects/{project_id}")
async def rename_project(
    project_id: uuid.UUID,
    body: ProjectPatch,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Rename a project."""
    project = await project_service.rename_project(session, principal, project_id, body.name)
    return project_service.project_to_dict(project)


@router.delete("/projects/{project_id}")
async def delete_project(
    project_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(db_session),
) -> dict:
    """Delete a project (memories keep their rows with project_id cleared)."""
    await project_service.delete_project(session, principal, project_id)
    return {"status": "ok"}
