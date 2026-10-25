from uuid import UUID

from fastapi import APIRouter

from app.api.deps import (
    CurrentUser,
    DbSession,
    ExecutorSvc,
    LLMSvc,
    QuerySvc,
    SchemaSvc,
)
from app.schemas.query import (
    AskQueryRequest,
    ExecuteQueryRequest,
    ExecuteQueryResponse,
    QueryResponse,
)
from app.services.query_executor import DEFAULT_ROW_LIMIT

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


@router.post("/{query_id}/execute", response_model=ExecuteQueryResponse)
async def execute_query(
    project_id: UUID,
    query_id: UUID,
    session: DbSession,
    schema_svc: SchemaSvc,
    svc: QuerySvc,
    executor: ExecutorSvc,
    user: CurrentUser,
    body: ExecuteQueryRequest | None = None,
) -> ExecuteQueryResponse:
    """Run a generated query against this project's database, and return the rows."""
    project = await schema_svc.owned_project(user, project_id)

    result = await svc.execute(
        project,
        query_id,
        limit=body.limit if body is not None else DEFAULT_ROW_LIMIT,
        confirmed=body.confirm if body is not None else False,
        executor=executor,
    )
    await session.commit()
    return ExecuteQueryResponse.model_validate(result)
