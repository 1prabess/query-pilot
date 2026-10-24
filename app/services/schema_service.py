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
from app.models.user import User
from app.services.project import get_owned_project
from app.services.schema_document import (
    SOURCE_DUMP,
    SOURCE_INTROSPECTION,
    build_document,
)
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

    async def upload(self, project: Project, raw: bytes) -> dict:
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
        await self.store(project, document, source=SOURCE_DUMP)
        return document

    async def introspect(self, project: Project) -> dict:
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
        await self.store(project, document, source=SOURCE_INTROSPECTION)
        return document

    async def get(
        self, project: Project, *, max_age_seconds: int | None = STALE_AFTER_SECONDS
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
                return await self.introspect(project)
            except CustomerDatabaseUnreachable:
                logger.warning(
                    "Schema refresh failed for project %s; serving the stored schema.",
                    project.id,
                )

        return project.schema_doc

    async def store(self, project: Project, document: dict, *, source: str) -> Project:
        project.schema_doc = document
        project.schema_source = source
        project.schema_fetched_at = datetime.now(UTC)
        await self.session.flush()
        return project

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
