import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import AppError
from app.models.project import Project
from app.models.team_membership import TeamMembership
from app.models.user import User
from app.services import permissions
from app.services.quota import QuotaService


class ProjectNotFound(AppError, status_code=404, code="project_not_found"):
    """No project with that id belongs to this user.

    404 for both "no such project" and "not your project"""


async def get_project_membership(
    session: AsyncSession,
    user: User,
    project_id: uuid.UUID,
    *,
    with_connection: bool = False,
) -> tuple[Project, TeamMembership]:
    """The project with that id, and the caller's membership of its team.

    A plain function rather than a method so that every service which needs a
    project reaches for the same lookup. Two services asking the same question
    must not answer it two different ways.

    The membership comes back with the project because the two are read
    together: what the caller may do to a project is a fact about their
    membership of the team that holds it, not about the project alone.
    """
    statement = (
        select(Project, TeamMembership)
        .join(TeamMembership, TeamMembership.team_id == Project.team_id)
        .where(Project.id == project_id, TeamMembership.user_id == user.id)
    )
    if with_connection:
        statement = statement.options(selectinload(Project.connection))

    row = (await session.execute(statement)).first()
    if row is None:
        raise ProjectNotFound("No such project.")
    return row[0], row[1]


class ProjectService:
    """Everything that reads or changes a team's projects.

    The session arrives in the constructor and every method uses it. Nothing
    in this class opens its own session.

    One instance per request, built by `get_project_service`. Not a singleton:
    it holds a session, and a session does not outlive a request.

    Methods do not commit. The caller owns the unit of work, so a route can
    compose several calls into one transaction.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.quota = QuotaService(session)

    async def create_project(
        self, membership: TeamMembership, name: str
    ) -> Project:
        """A new project in the team the membership belongs to."""
        allowance = await self.quota.allowance_for_team(membership.team_id)
        self.quota.assert_can_create_project(allowance)

        project = Project(team_id=membership.team_id, name=name.strip())
        self.session.add(project)

        await self.session.flush()
        return project

    async def list_projects(self, user: User) -> list[Project]:
        """Every project in every team the caller belongs to, oldest first.

        The connections are not loaded, and that is the point of keeping the
        credential in its own table: listing projects must not decrypt a
        credential it has no reason to read.
        """
        result = await self.session.scalars(
            select(Project)
            .join(TeamMembership, TeamMembership.team_id == Project.team_id)
            .where(TeamMembership.user_id == user.id)
            .order_by(Project.created_at)
        )
        return list(result.all())

    async def get_project(
        self, user: User, project_id: uuid.UUID, *, with_connection: bool = False
    ) -> tuple[Project, TeamMembership]:
        return await get_project_membership(
            self.session, user, project_id, with_connection=with_connection
        )

    async def set_connection(
        self,
        project: Project,
        membership: TeamMembership,
        *,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
        schema_name: str,
    ) -> Project:
        """Point the project at a database, replacing any previous credential.

        The password is write-only. It goes into the encrypted column and is
        never read back: `connection_details` returns everything else about
        the connection, and the only way to get the password out is to decrypt
        the row, which happens when a query actually needs to run.
        """
        permissions.require(membership.role, permissions.MANAGE_DATABASES)
        project.set_connection(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            schema_name=schema_name,
        )
        await self.session.flush()
        return project

    async def connection_details(
        self, project: Project, membership: TeamMembership
    ) -> dict:
        """The stored connection, minus the password."""
        permissions.require(membership.role, permissions.MANAGE_DATABASES)
        return project.connection_details()

    async def set_writes_enabled(
        self,
        project: Project,
        membership: TeamMembership,
        *,
        enabled: bool,
    ) -> Project:
        permissions.require(membership.role, permissions.MANAGE_DATABASES)
        project.writes_enabled = enabled
        await self.session.flush()

        await self.session.refresh(project, ["updated_at"])
        return project

    async def delete_project(
        self, project: Project, membership: TeamMembership
    ) -> None:
        permissions.require(membership.role, permissions.MANAGE_DATABASES)
        await self.session.delete(project)
        await self.session.flush()
