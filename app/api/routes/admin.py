from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import AdminSvc, AdminUser, DbSession, SubscriptionSvc
from app.core.errors import UserNotFound
from app.models.user import User
from app.schemas.admin import AdminUserDetail, AdminUserListResponse, AdminUserSummary
from app.schemas.subscription import (
    PlanResponse,
    SetPlanRequest,
    SubscriptionResponse,
    UsageResponse,
)
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

    summary = AdminUserSummary.model_validate(detail.summary)
    return AdminUserDetail(
        **summary.model_dump(),
        usage=UsageResponse.of(detail.allowance),
        provider_customer_id=detail.provider_customer_id,
        provider_subscription_id=detail.provider_subscription_id,
    )


@router.patch("/users/{user_id}/subscription", response_model=SubscriptionResponse)
async def set_user_plan(
    user_id: UUID,
    body: SetPlanRequest,
    session: DbSession,
    svc: SubscriptionSvc,
    admin: AdminUser,
) -> SubscriptionResponse:
    """Put a user on a plan.

    This is how a plan changes today, because there is no payment provider
    to say so yet. It is deliberately the same operation a payment webhook
    will perform when one is connected: set a tier, set a status, set the
    period usage is counted over. The provider fills in its own columns and
    nothing else in the product has to change.
    """
    target = await session.get(User, user_id)
    if target is None:
        raise UserNotFound("No such user.")

    subscription = await svc.set_plan(
        target.id,
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
