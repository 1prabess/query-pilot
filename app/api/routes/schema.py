from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Query, UploadFile

from app.api.deps import CurrentUser, DbSession, SchemaSvc
from app.schemas.schema import (
    SchemaChangeListResponse,
    SchemaChangeResponse,
    SchemaDocument,
    SchemaVersionListResponse,
    SchemaVersionResponse,
)
from app.services.schema_diff import summarize
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

    document = await svc.upload(project, raw, captured_by=user.id)
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

    document = await svc.introspect(project, captured_by=user.id)
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

    document = await svc.get(project, captured_by=user.id)
    await session.commit()
    return SchemaDocument.model_validate(document)


@router.get("/versions", response_model=SchemaVersionListResponse)
async def list_schema_versions(
    project_id: UUID,
    session: DbSession,
    svc: SchemaSvc,
    user: CurrentUser,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> SchemaVersionListResponse:
    """The states this project's schema has been in.

    A version is written when the schema changes, so this is the list of
    changes with the schema each of them produced.
    """
    await svc.owned_project(user, project_id)

    versions, total = await svc.versions(project_id, limit=limit, offset=offset)
    return SchemaVersionListResponse(
        total=total,
        limit=limit,
        offset=offset,
        versions=[
            SchemaVersionResponse.model_validate(version) for version in versions
        ],
    )


@router.get("/changes", response_model=SchemaChangeListResponse)
async def list_schema_changes(
    project_id: UUID,
    session: DbSession,
    svc: SchemaSvc,
    user: CurrentUser,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> SchemaChangeListResponse:
    """What has changed about this project's schema, and what it breaks.

    A breaking change names the saved questions that referred to the column
    it touched, because those are the answers that will now fail.
    """
    await svc.owned_project(user, project_id)

    changes, total = await svc.changes(project_id, limit=limit, offset=offset)
    affected = await svc.impact(project_id, changes)

    return SchemaChangeListResponse(
        total=total,
        limit=limit,
        offset=offset,
        changes=[
            SchemaChangeResponse(
                id=change.id,
                version=change.version,
                kind=change.kind,
                severity=change.severity,
                summary=summarize(
                    change.kind,
                    change.table_name,
                    change.column_name,
                    change.detail,
                ),
                table_name=change.table_name,
                column_name=change.column_name,
                detail=change.detail,
                affected_queries=affected.get(change.id, []),
                created_at=change.created_at,
            )
            for change in changes
        ],
    )
