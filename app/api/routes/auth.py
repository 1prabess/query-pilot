import logging

from fastapi import APIRouter, Response, status

from app.api.deps import AuthSvc, CurrentUser, DbSession
from app.core.config import get_settings
from app.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    MagicLinkRequest,
    MagicLinkRequestedResponse,
    MagicLinkVerifyRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.services.auth import TokenPair

router = APIRouter(prefix="/auth", tags=["auth"])

logger = logging.getLogger(__name__)
settings = get_settings()


def _pair_response(pair: TokenPair) -> TokenResponse:
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
        user=UserResponse.model_validate(pair.user),
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest, session: DbSession, svc: AuthSvc
) -> TokenResponse:
    pair = await svc.register_user(body.email, body.password)
    await session.commit()
    return _pair_response(pair)


@router.post("/login", response_model=TokenResponse, status_code=status.HTTP_200_OK)
async def login(
    body: LoginRequest, session: DbSession, svc: AuthSvc
) -> TokenResponse:
    pair = await svc.authenticate(body.email, body.password)
    await session.commit()
    return _pair_response(pair)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest, session: DbSession, svc: AuthSvc
) -> TokenResponse:
    pair = await svc.refresh(body.refresh_token)
    await session.commit()
    return _pair_response(pair)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    body: LogoutRequest, session: DbSession, svc: AuthSvc
) -> Response:
    await svc.logout(body.refresh_token)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserResponse)
async def me(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)


@router.post(
    "/magic-link",
    response_model=MagicLinkRequestedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_magic_link(
    body: MagicLinkRequest, session: DbSession, svc: AuthSvc
) -> MagicLinkRequestedResponse:
    issued = await svc.request_magic_link(body.email)
    await session.commit()

    # The response is byte-for-byte the same for a real address and a made-up
    # one, so it cannot be used to find out who has an account. The raw token
    # only surfaces on a local log line; in staging and production nothing
    # leaves the database until a real mailer is wired up.
    if issued is not None and settings.ENVIRONMENT == "local":
        user, raw = issued
        logger.info("magic link issued for user_id=%s token=%s", user.id, raw)

    return MagicLinkRequestedResponse()


@router.post("/magic-link/verify", response_model=TokenResponse)
async def verify_magic_link(
    body: MagicLinkVerifyRequest, session: DbSession, svc: AuthSvc
) -> TokenResponse:
    user = await svc.consume_magic_link(body.token)
    pair = await svc.issue_for(user)
    await session.commit()
    return _pair_response(pair)
