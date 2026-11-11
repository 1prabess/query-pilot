from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import AdminSvc, AdminUser, DbSession, SubscriptionSvc
from app.core.errors import CannotRevokeSelf, TeamNotFound, UserNotFound
from app.models.team import Team
from app.models.team_membership import OWNER, TeamMembership
from app.models.user import User
from app.schemas.admin import (
    AdminTeamDetail,
    AdminTeamListResponse,
    AdminTeamSummary,
    AdminUserDetail,
    AdminUserListResponse,
    AdminUserSummary,
    SetRevokedRequest,
)
from app.schemas.subscription import (
    PlanResponse,
    SetPlanRequest,
    SubscriptionResponse,
    UsageResponse,
)
from app.services.admin import TeamDetail, UserDetail
from app.services.plans import get_plan

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users", response_model=AdminUserListResponse)
async def list_users(
    svc: AdminSvc,
    admin: AdminUser,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AdminUserListResponse:
    """Every account, the plan it is on, and what it has used.

    The counts are totals, not this period's usage: the period belongs to
    each subscription, so the number that depends on it is on the account's
    own page.
    """
    summaries, total = await svc.list_users(limit=limit, offset=offset)
    return AdminUserListResponse(
        total=total,
        limit=limit,
        offset=offset,
        users=[AdminUserSummary.model_validate(summary) for summary in summaries],
    )


@router.get("/users/{user_id}", response_model=AdminUserDetail)
async def get_user(
    user_id: UUID, svc: AdminSvc, admin: AdminUser
) -> AdminUserDetail:
    """One account, and everything counted against it.

    This is where the usage for the account's own period lives, because one
    window cannot count a premium customer against the calendar month.
    """
    detail = await svc.get_user(user_id)
    if detail is None:
        raise UserNotFound("No such user.")

    return _detail_response(detail)


@router.patch("/users/{user_id}", response_model=AdminUserDetail)
async def set_user_revoked(
    user_id: UUID,
    body: SetRevokedRequest,
    session: DbSession,
    svc: AdminSvc,
    admin: AdminUser,
) -> AdminUserDetail:
    """Close an account, or reopen it.

    Closing is instant: the account row is read on every request, so the
    next one is turned away even though its token has not expired.
    """
    if body.revoked and user_id == admin.id:
        raise CannotRevokeSelf("You cannot revoke your own account.")

    detail = await svc.set_revoked(
        user_id,
        revoked=body.revoked,
        reason=body.reason,
        revoked_by=admin.id,
    )
    if detail is None:
        raise UserNotFound("No such user.")

    await session.commit()
    return _detail_response(detail)


@router.patch("/users/{user_id}/subscription", response_model=SubscriptionResponse)
async def set_user_plan(
    user_id: UUID,
    body: SetPlanRequest,
    session: DbSession,
    svc: SubscriptionSvc,
    admin: AdminUser,
) -> SubscriptionResponse:
    """Put an account on a plan.

    A plan belongs to a team, so this is the plan of the team the account
    owns first -- the one it registered with. Use the team route when the
    team matters more than the account.

    This is how a plan changes today, because there is no payment provider
    to say so yet. It is deliberately the same operation a payment webhook
    will perform when one is connected: set a tier, set a status, set the
    period usage is counted over. The provider fills in its own columns and
    nothing else in the product has to change.
    """
    target = await session.get(User, user_id)
    if target is None:
        raise UserNotFound("No such user.")

    team_id = await _first_owned_team_id(session, target.id)
    if team_id is None:
        raise UserNotFound("This account does not own a team.")

    subscription = await svc.set_plan(
        team_id,
        tier=body.tier,
        status=body.status,
        period_start=body.period_start,
        period_end=body.period_end,
    )
    await session.commit()

    plan = get_plan(subscription.tier)
    return SubscriptionResponse(
        tier=subscription.tier,
        status=subscription.status,
        period_start=subscription.period_start,
        period_end=subscription.period_end,
        plan=PlanResponse.of(plan),
    )


@router.get("/teams", response_model=AdminTeamListResponse)
async def list_teams(
    svc: AdminSvc,
    admin: AdminUser,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AdminTeamListResponse:
    """Every team, the plan it is on, and what it has used."""
    summaries, total = await svc.list_teams(limit=limit, offset=offset)
    return AdminTeamListResponse(
        total=total,
        limit=limit,
        offset=offset,
        teams=[AdminTeamSummary.model_validate(summary) for summary in summaries],
    )


@router.get("/teams/{team_id}", response_model=AdminTeamDetail)
async def get_team(
    team_id: UUID, svc: AdminSvc, admin: AdminUser
) -> AdminTeamDetail:
    """One team, and everything counted against it."""
    detail = await svc.get_team(team_id)
    if detail is None:
        raise TeamNotFound("No such team.")
    return _team_detail_response(detail)


@router.patch("/teams/{team_id}/subscription", response_model=SubscriptionResponse)
async def set_team_plan(
    team_id: UUID,
    body: SetPlanRequest,
    session: DbSession,
    svc: SubscriptionSvc,
    admin: AdminUser,
) -> SubscriptionResponse:
    """Put a team on a plan."""
    team = await session.get(Team, team_id)
    if team is None:
        raise TeamNotFound("No such team.")

    subscription = await svc.set_plan(
        team.id,
        tier=body.tier,
        status=body.status,
        period_start=body.period_start,
        period_end=body.period_end,
    )
    await session.commit()

    plan = get_plan(subscription.tier)
    return SubscriptionResponse(
        tier=subscription.tier,
        status=subscription.status,
        period_start=subscription.period_start,
        period_end=subscription.period_end,
        plan=PlanResponse.of(plan),
    )


def _detail_response(detail: UserDetail) -> AdminUserDetail:
    summary = AdminUserSummary.model_validate(detail.summary)
    return AdminUserDetail(
        **summary.model_dump(),
        usage=UsageResponse.of(detail.allowance),
        provider_customer_id=detail.provider_customer_id,
        provider_subscription_id=detail.provider_subscription_id,
    )


def _team_detail_response(detail: TeamDetail) -> AdminTeamDetail:
    summary = AdminTeamSummary.model_validate(detail.summary)
    return AdminTeamDetail(
        **summary.model_dump(),
        usage=UsageResponse.of(detail.allowance),
        provider_customer_id=detail.provider_customer_id,
        provider_subscription_id=detail.provider_subscription_id,
    )


async def _first_owned_team_id(session: DbSession, user_id: UUID) -> UUID | None:
    return await session.scalar(
        select(TeamMembership.team_id)
        .where(
            TeamMembership.user_id == user_id,
            TeamMembership.role == OWNER,
        )
        .order_by(TeamMembership.created_at)
        .limit(1)
    )
