from uuid import UUID

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession, LLMSvc, QuerySvc, SchemaSvc
from app.schemas.query import AskQueryRequest, QueryResponse

router = APIRouter(prefix="/projects/{project_id}/query", tags=["query"])


@router.post("", response_model=QueryResponse)
async def ask_query(
    project_id: UUID,
    body: AskQueryRequest,
    session: DbSession,
    schema_svc: SchemaSvc,
    svc: QuerySvc,
    llm: LLMSvc,
    user: CurrentUser,
) -> QueryResponse:
    """Answer a question about this project's database, in SQL."""
    project = await schema_svc.owned_project(user, project_id)
    schema_doc = await schema_svc.get(project)

    answer = await svc.ask(
        project, body.question, schema_doc=schema_doc, llm=llm
    )
    await session.commit()
    return QueryResponse.model_validate(answer)
