from uuid import UUID

from fastapi import APIRouter

from app.api.deps import (
    AnalyticsSvc,
    CurrentUser,
    DbSession,
    ExecutorSvc,
    LLMSvc,
    SchemaSvc,
)
from app.schemas.analytics import AnalyticsRequest, AnalyticsResponse

router = APIRouter(prefix="/projects/{project_id}/analytics", tags=["analytics"])


@router.post("", response_model=AnalyticsResponse)
async def analyze(
    project_id: UUID,
    body: AnalyticsRequest,
    session: DbSession,
    schema_svc: SchemaSvc,
    svc: AnalyticsSvc,
    executor: ExecutorSvc,
    llm: LLMSvc,
    user: CurrentUser,
) -> AnalyticsResponse:
    """Answer a question about this project's database, with charts.

    Every chart is one statement, run on its own against the customer's
    database. A chart whose statement fails comes back carrying the reason,
    and the charts around it are unaffected: one broken query is not a broken
    answer.
    """
    project, membership = await schema_svc.project_for(user, project_id)
    schema_doc = await schema_svc.get(project, captured_by=user.id)

    answer = await svc.answer(
        project,
        membership,
        body.question,
        schema_doc=schema_doc,
        llm=llm,
        executor=executor,
    )
    await session.commit()
    return AnalyticsResponse.model_validate(answer)
