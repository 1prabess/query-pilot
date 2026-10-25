from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AdminRequired
from app.core.security import decode_access_token
from app.db.session import get_db
from app.llm.service import LLMService, get_llm_service
from app.models.user import User
from app.services.auth import AuthError, AuthService
from app.services.project import ProjectService
from app.services.query import QueryService
from app.services.query_executor import QueryExecutor, get_query_executor
from app.services.quota import QuotaService
from app.services.schema_service import SchemaService
from app.services.subscription import SubscriptionService

bearer = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_db)]


def get_auth_service(session: DbSession) -> AuthService:
    return AuthService(session)


AuthSvc = Annotated[AuthService, Depends(get_auth_service)]


def get_project_service(session: DbSession) -> ProjectService:
    return ProjectService(session)


ProjectSvc = Annotated[ProjectService, Depends(get_project_service)]


def get_schema_service(session: DbSession) -> SchemaService:
    return SchemaService(session)


SchemaSvc = Annotated[SchemaService, Depends(get_schema_service)]


def get_query_service(session: DbSession) -> QueryService:
    return QueryService(session)


QuerySvc = Annotated[QueryService, Depends(get_query_service)]


def get_subscription_service(session: DbSession) -> SubscriptionService:
    return SubscriptionService(session)


SubscriptionSvc = Annotated[SubscriptionService,
                            Depends(get_subscription_service)]


def get_quota_service(session: DbSession) -> QuotaService:
    return QuotaService(session)


QuotaSvc = Annotated[QuotaService, Depends(get_quota_service)]

# The executor opens and closes its own connections to the customer's
# database, so it has no session of ours to hold.
ExecutorSvc = Annotated[QueryExecutor, Depends(get_query_executor)]

# The LLM service is built on first use and shared by every request, so it
# takes no session -- it has no database of its own to close.
LLMSvc = Annotated[LLMService, Depends(get_llm_service)]


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    session: DbSession,
) -> User:
    if credentials is None:
        raise AuthError("Not authenticated.")

    try:
        user_id = decode_access_token(credentials.credentials)
    except Exception:
        raise AuthError("Invalid or expired token.")

    user = await session.get(User, user_id)

    if user is None or not user.is_active:
        raise AuthError("Invalid or expired token.")

    return user

CurrentUser = Annotated[User, Depends(get_current_user)]


def _is_admin(user: User) -> bool:
    return user.role == "admin"


def get_current_admin(user: CurrentUser) -> User:
    if not _is_admin(user):
        raise AdminRequired("This action is for administrators")
    return user


AdminUser = Annotated[User, Depends(get_current_admin)]
