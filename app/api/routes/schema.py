from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, UploadFile

from app.api.deps import CurrentUser, DbSession, SchemaSvc
from app.schemas.schema import SchemaDocument
from app.services.schema_service import MAX_UPLOAD_BYTES

router = APIRouter(prefix="/projects/{project_id}/schema", tags=["schema"])


@router.post(
    "/upload",
    response_model=SchemaDocument,
    response_model_exclude_none=True,
)
async def upload_schema(
    project_id: UUID,
    file: Annotated[UploadFile, File()],
    session: DbSession,
    svc: SchemaSvc,
    user: CurrentUser,
) -> SchemaDocument:
    """Read a `pg_dump` of the customer's database and store it as the schema."""
    project = await svc.owned_project(user, project_id)
    raw = await file.read(MAX_UPLOAD_BYTES + 1)

    document = await svc.upload(project, raw)
    await session.commit()
    return SchemaDocument.model_validate(document)


@router.post(
    "/introspect",
    response_model=SchemaDocument,
    response_model_exclude_none=True,
)
async def introspect_schema(
    project_id: UUID,
    session: DbSession,
    svc: SchemaSvc,
    user: CurrentUser,
) -> SchemaDocument:
    """Ask the customer's database what its schema is, and store the answer."""
    project = await svc.owned_project(user, project_id)

    document = await svc.introspect(project)
    await session.commit()
    return SchemaDocument.model_validate(document)


@router.get(
    "",
    response_model=SchemaDocument,
    response_model_exclude_none=True,
)
async def get_schema(
    project_id: UUID,
    session: DbSession,
    svc: SchemaSvc,
    user: CurrentUser,
) -> SchemaDocument:
    """The schema we have stored for this project.

    This reads, but it can also write: a schema that came from a live
    introspection is refreshed from the customer's database once it is an hour
    old, and that refresh has to be persisted.
    """
    project = await svc.owned_project(user, project_id)

    document = await svc.get(project)
    await session.commit()
    return SchemaDocument.model_validate(document)
