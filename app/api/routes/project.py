from uuid import UUID

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentUser, DbSession, ProjectSvc
from app.schemas.project import (
    ConnectionResponse,
    CreateProjectRequest,
    ProjectResponse,
    SetConnectionRequest,
    SetWritesEnabledRequest,
)

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: CreateProjectRequest, session: DbSession, svc: ProjectSvc, user: CurrentUser
) -> ProjectResponse:
    project = await svc.create_project(user, body.name)
    await session.commit()
    return ProjectResponse.model_validate(project)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    svc: ProjectSvc, user: CurrentUser
) -> list[ProjectResponse]:
    projects = await svc.list_projects(user)
    return [ProjectResponse.model_validate(project) for project in projects]


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,  svc: ProjectSvc, user: CurrentUser
) -> ProjectResponse:
    project = await svc.get_project(user, project_id)
    return ProjectResponse.model_validate(project)


@router.put("/{project_id}/connection", response_model=ConnectionResponse)
async def set_connection(
    project_id: UUID,
    body: SetConnectionRequest,
    session: DbSession,
    svc: ProjectSvc,
    user: CurrentUser,
) -> ConnectionResponse:
    project = await svc.set_connection(
        user,
        project_id,
        host=body.host,
        port=body.port,
        database=body.database,
        user=body.user,
        password=body.password,
        schema_name=body.schema_name,
    )
    await session.commit()
    return ConnectionResponse(**project.connection_details())


@router.get("/{project_id}/connection", response_model=ConnectionResponse)
async def get_connection(
    project_id: UUID, svc: ProjectSvc, user: CurrentUser
) -> ConnectionResponse:
    details = await svc.connection_details(user, project_id)
    return ConnectionResponse(**details)


@router.patch("/{project_id}/writes", response_model=ProjectResponse)
async def set_writes(
    project_id: UUID,
    body: SetWritesEnabledRequest,
    session: DbSession,
    svc: ProjectSvc,
    user: CurrentUser,
) -> ProjectResponse:
    project = await svc.set_writes_enabled(user, project_id, enabled=body.enabled)
    await session.commit()
    return ProjectResponse.model_validate(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID, session: DbSession, svc: ProjectSvc, user: CurrentUser
) -> Response:
    await svc.delete_project(user, project_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
