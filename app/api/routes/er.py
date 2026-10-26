from uuid import UUID

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession, ErSvc, LLMSvc, SchemaSvc
from app.schemas.er import ErModel

router = APIRouter(prefix="/projects/{project_id}/er-diagram", tags=["er"])


@router.get("", response_model=ErModel)
async def get_er_diagram(
    project_id: UUID,
    session: DbSession,
    schema_svc: SchemaSvc,
    svc: ErSvc,
    user: CurrentUser,
    llm: LLMSvc,
) -> ErModel:
    """The entity-relationship diagram of this project's schema.

    The entities, the attributes and the relationships are worked out from
    the schema, so they are the same every time and they are right. The
    labels -- what a relationship is called, which attributes are computed
    rather than stored -- come from the model when there is one, and the
    diagram is drawn without them when there is not.

    Nothing in the answer is a coordinate. The client lays the diagram out,
    so one model can be drawn on a page and printed on a sheet.
    """
    project = await schema_svc.owned_project(user, project_id)
    schema_doc = await schema_svc.get(project, captured_by=user.id)
    await session.commit()

    return await svc.diagram(schema_doc, llm=llm)
