"""A project's schema, however it was read.

A project can learn its schema in two ways. The customer can send us a
`pg_dump` of their database, which is the only option for a customer who will
not hand over a connection. Or we can ask their database ourselves, over the
connection they gave us.

Both ways end in the same place: a document stored on the project, describing
the same tables, in the same shape, whichever way it was read. That is the
whole point of the two readers agreeing -- a consumer of the document should
not have to know where it came from.

The session arrives in the constructor and every method uses it. Methods do
not commit; the caller owns the unit of work.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    CustomerDatabaseUnreachable,
    NoConnection,
    NoSchemaStored,
    NoSchemaTables,
    SchemaParseFailed,
    SchemaTooLarge,
)
from app.models.project import Project
from app.models.schema_change import SchemaChange
from app.models.schema_version import SchemaVersion
from app.models.user import User
from app.services.project import get_owned_project
from app.services.schema_diff import BREAKING, diff_documents
from app.services.schema_document import (
    SOURCE_DUMP,
    SOURCE_INTROSPECTION,
    build_document,
)
from app.services.schema_impact import affected_queries, column_references
from app.services.schema_introspector import IntrospectionError, introspect_schema
from app.services.schema_parser import SchemaParseError, parse_pg_dump

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
STALE_AFTER_SECONDS = 3600


class SchemaService:
    """Everything that reads or stores a project's schema."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def owned_project(self, owner: User, project_id: uuid.UUID) -> Project:
        """The project, with its credential loaded.

        The credential is loaded because both readers need it: the live one to
        connect, and the dump one to know which schema the project is about.
        """
        return await get_owned_project(
            self.session, owner, project_id, with_connection=True
        )

    async def upload(
        self, project: Project, raw: bytes, *, captured_by: uuid.UUID | None = None
    ) -> dict:
        """Read a dump and store it as this project's schema."""
        if len(raw) > MAX_UPLOAD_BYTES:
            raise SchemaTooLarge("The upload is over the size limit.")

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SchemaParseFailed("The upload is not a text file.") from exc

        try:
            tables = parse_pg_dump(text, schema_name=self._schema_name(project))
        except SchemaParseError as exc:
            raise SchemaParseFailed(str(exc)) from exc

        if not tables:
            raise NoSchemaTables("The upload held no tables for this project.")

        document = build_document(SOURCE_DUMP, tables)
        await self.store(
            project, document, source=SOURCE_DUMP, captured_by=captured_by
        )
        return document

    async def introspect(
        self, project: Project, *, captured_by: uuid.UUID | None = None
    ) -> dict:
        """Ask the customer's database what its schema is, and store it."""
        if project.connection is None:
            raise NoConnection("This project has no database connection saved yet.")

        schema_name = project.connection.schema_name
        try:
            tables = await introspect_schema(project.decrypted_dsn(), schema_name)
        except IntrospectionError as exc:
            raise CustomerDatabaseUnreachable(
                "We could not read the customer's database."
            ) from exc

        document = build_document(SOURCE_INTROSPECTION, tables)
        await self.store(
            project, document, source=SOURCE_INTROSPECTION, captured_by=captured_by
        )
        return document

    async def get(
        self,
        project: Project,
        *,
        max_age_seconds: int | None = STALE_AFTER_SECONDS,
        captured_by: uuid.UUID | None = None,
    ) -> dict:
        """The stored schema, refreshed if it has gone stale.

        Only a schema that came from a live introspection goes stale. A schema
        that came from a dump is as true as the day it was uploaded -- there is
        nothing to re-read it from.

        A refresh that fails leaves the stored document alone. The customer may
        have taken their database away, and that is not a reason to lose the
        schema we already have.
        """
        if not project.schema_doc:
            raise NoSchemaStored("This project has no schema yet.")

        if self._is_stale(project, max_age_seconds):
            try:
                return await self.introspect(project, captured_by=captured_by)
            except CustomerDatabaseUnreachable:
                logger.warning(
                    "Schema refresh failed for project %s; serving the stored schema.",
                    project.id,
                )

        return project.schema_doc

    async def store(
        self,
        project: Project,
        document: dict,
        *,
        source: str,
        captured_by: uuid.UUID | None = None,
    ) -> Project:
        """Store the document, and record what changed since the last one.

        A schema that has not changed does not get a new version. The version
        table holds the states a project has been in, not the number of times
        it was looked at.

        A project that has a schema but no versions gets one now, whatever
        the schema looks like: its first version is the baseline everything
        else is measured against.
        """
        previous = project.schema_doc
        differences = diff_documents(previous, document)

        project.schema_doc = document
        project.schema_source = source
        project.schema_fetched_at = datetime.now(UTC)
        await self.session.flush()

        latest = await self.session.scalar(
            select(func.max(SchemaVersion.version)).where(
                SchemaVersion.project_id == project.id
            )
        )
        if latest is None or differences:
            await self._record(
                project, document, source, differences, captured_by, int(latest or 0) + 1
            )

        return project

    async def versions(
        self, project_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[SchemaVersion], int]:
        """The states this project's schema has been in, newest first."""
        rows = await self.session.scalars(
            select(SchemaVersion)
            .where(SchemaVersion.project_id == project_id)
            .order_by(SchemaVersion.version.desc())
            .limit(limit)
            .offset(offset)
        )
        total = await self.session.scalar(
            select(func.count())
            .select_from(SchemaVersion)
            .where(SchemaVersion.project_id == project_id)
        )
        return list(rows), int(total or 0)

    async def changes(
        self, project_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[SchemaChange], int]:
        """The changes this project's schema has been through, newest first."""
        rows = await self.session.scalars(
            select(SchemaChange)
            .where(SchemaChange.project_id == project_id)
            .order_by(SchemaChange.version.desc(), SchemaChange.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        total = await self.session.scalar(
            select(func.count())
            .select_from(SchemaChange)
            .where(SchemaChange.project_id == project_id)
        )
        return list(rows), int(total or 0)

    async def impact(
        self, project_id: uuid.UUID, changes: list[SchemaChange]
    ) -> dict[uuid.UUID, list[uuid.UUID]]:
        """Which saved queries each breaking change breaks.

        Only the breaking ones. An added column breaks nothing that was
        there before it.
        """
        breaking = [change for change in changes if change.severity == BREAKING]
        if not breaking:
            return {}

        references = await column_references(self.session, project_id)
        return {
            change.id: affected_queries(
                references, change.table_name, change.column_name
            )
            for change in breaking
        }

    async def _record(
        self,
        project: Project,
        document: dict,
        source: str,
        differences: list,
        captured_by: uuid.UUID | None,
        version: int,
    ) -> None:
        """Write the version, and the changes that came with it."""
        self.session.add(
            SchemaVersion(
                project_id=project.id,
                version=version,
                captured_by=captured_by,
                source=source,
                document=document,
                change_count=len(differences),
            )
        )
        for difference in differences:
            self.session.add(
                SchemaChange(
                    project_id=project.id,
                    version=version,
                    kind=difference.kind,
                    severity=difference.severity,
                    table_name=difference.table,
                    column_name=difference.column,
                    detail=difference.detail,
                )
            )

    def _is_stale(
        self, project: Project, max_age_seconds: int | None
    ) -> bool:
        if max_age_seconds is None or project.schema_source != SOURCE_INTROSPECTION:
            return False
        if project.schema_fetched_at is None:
            return True
        age = datetime.now(UTC) - project.schema_fetched_at
        return age > timedelta(seconds=max_age_seconds)

    def _schema_name(self, project: Project) -> str | None:
        """The schema this project is about, if we know it.

        A project with no connection has no schema yet, and a dump uploaded
        before the connection is set may well describe any schema, so nothing
        is filtered out in that case.
        """
        if project.connection is None:
            return None
        return project.connection.schema_name
