import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import AppError
from app.models.project import Project
from app.models.user import User


class ProjectNotFound(AppError, status_code=404, code="project_not_found"):
    """No project with that id belongs to this user.

    404 for both "no such project" and "not your project"""


async def get_owned_project(
    session: AsyncSession,
    owner: User,
    project_id: uuid.UUID,
    *,
    with_connection: bool = False,
) -> Project:
    """The project with that id, if it belongs to `owner`.

    A plain function rather than a method so that every service which needs a
    project reaches for the same lookup. Two services asking the same question
    must not answer it two different ways.
    """
    statement = select(Project).where(
        Project.id == project_id,
        Project.owner_id == owner.id,
    )
    if with_connection:
        statement = statement.options(selectinload(Project.connection))

    project = await session.scalar(statement)
    if project is None:
        raise ProjectNotFound("No such project.")
    return project


class ProjectService:
    """Everything that reads or changes a user's projects.

    The session arrives in the constructor and every method uses it. Nothing
    in this class opens its own session.

    One instance per request, built by `get_project_service`. Not a singleton:
    it holds a session, and a session does not outlive a request.

    Methods do not commit. The caller owns the unit of work, so a route can
    compose several calls into one transaction.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_project(self, owner: User, name: str) -> Project:
        project = Project(owner_id=owner.id, name=name.strip())
        self.session.add(project)

        await self.session.flush()
        return project

    async def list_projects(self, owner: User) -> list[Project]:
        """Every project the owner has, oldest first.

        The connections are not loaded, and that is the point of keeping the
        credential in its own table: listing projects must not decrypt a
        credential it has no reason to read.
        """
        result = await self.session.scalars(
            select(Project)
            .where(Project.owner_id == owner.id)
            .order_by(Project.created_at)
        )
        return list(result.all())

    async def get_project(self, owner: User, project_id: uuid.UUID) -> Project:
        return await self._owned_project(owner, project_id)

    async def set_connection(
        self,
        owner: User,
        project_id: uuid.UUID,
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
        project = await self._owned_project(owner, project_id, with_connection=True)
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
        self, owner: User, project_id: uuid.UUID
    ) -> dict:
        """The stored connection, minus the password."""
        project = await self._owned_project(owner, project_id, with_connection=True)
        return project.connection_details()

    async def set_writes_enabled(
        self, owner: User, project_id: uuid.UUID, *, enabled: bool
    ) -> Project:
        project = await self._owned_project(owner, project_id)
        project.writes_enabled = enabled
        await self.session.flush()

        await self.session.refresh(project, ["updated_at"])
        return project

    async def delete_project(self, owner: User, project_id: uuid.UUID) -> None:
        project = await self._owned_project(owner, project_id)
        await self.session.delete(project)
        await self.session.flush()

    async def _owned_project(
        self, owner: User, project_id: uuid.UUID, *, with_connection: bool = False
    ) -> Project:
        return await get_owned_project(
            self.session, owner, project_id, with_connection=with_connection
        )
