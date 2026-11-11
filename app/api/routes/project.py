from uuid import UUID

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentUser, DbSession, ProjectSvc, TeamSvc
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
    body: CreateProjectRequest,
    session: DbSession,
    svc: ProjectSvc,
    teams: TeamSvc,
    user: CurrentUser,
) -> ProjectResponse:
    """A new project in one of the caller's teams."""
    membership = await teams.resolve_membership(user.id, body.team_id)
    project = await svc.create_project(membership, body.name)
    await session.commit()
    return ProjectResponse.model_validate(project)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    svc: ProjectSvc, user: CurrentUser
) -> list[ProjectResponse]:
    """Every project in every team the caller belongs to."""
    projects = await svc.list_projects(user)
    return [ProjectResponse.model_validate(project) for project in projects]


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,  svc: ProjectSvc, user: CurrentUser
) -> ProjectResponse:
    project, _ = await svc.get_project(user, project_id)
    return ProjectResponse.model_validate(project)


@router.put("/{project_id}/connection", response_model=ConnectionResponse)
async def set_connection(
    project_id: UUID,
    body: SetConnectionRequest,
    session: DbSession,
    svc: ProjectSvc,
    user: CurrentUser,
) -> ConnectionResponse:
    project, membership = await svc.get_project(
        user, project_id, with_connection=True
    )
    project = await svc.set_connection(
        project,
        membership,
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
    project, membership = await svc.get_project(
        user, project_id, with_connection=True
    )
    details = await svc.connection_details(project, membership)
    return ConnectionResponse(**details)


@router.patch("/{project_id}/writes", response_model=ProjectResponse)
async def set_writes(
    project_id: UUID,
    body: SetWritesEnabledRequest,
    session: DbSession,
    svc: ProjectSvc,
    user: CurrentUser,
) -> ProjectResponse:
    project, membership = await svc.get_project(user, project_id)
    project = await svc.set_writes_enabled(
        project, membership, enabled=body.enabled
    )
    await session.commit()
    return ProjectResponse.model_validate(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID, session: DbSession, svc: ProjectSvc, user: CurrentUser
) -> Response:
    project, membership = await svc.get_project(user, project_id)
    await svc.delete_project(project, membership)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
