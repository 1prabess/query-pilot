from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.user import User
from app.services.auth import AuthError, AuthService

bearer = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_db)]


def get_auth_service(session: DbSession) -> AuthService:
    return AuthService(session)


AuthSvc = Annotated[AuthService, Depends(get_auth_service)]


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
