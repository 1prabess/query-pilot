from fastapi import APIRouter

from app.api.deps import CurrentUser, QuotaSvc, SubscriptionSvc
from app.schemas.subscription import (
    PlanResponse,
    SubscriptionResponse,
    UsageResponse,
)
from app.services.plans import PLANS

plans_router = APIRouter(prefix="/plans", tags=["plans"])
router = APIRouter(prefix="/me", tags=["subscription"])


@plans_router.get("", response_model=list[PlanResponse])
async def list_plans() -> list[PlanResponse]:
    """Every plan, and what it allows.

    No sign-in needed: this is the pricing table, and it says nothing about
    who is asking.
    """
    return [PlanResponse.of(plan) for plan in PLANS.values()]


@router.get("/subscription", response_model=SubscriptionResponse)
async def my_subscription(
    user: CurrentUser, svc: SubscriptionSvc, quota: QuotaSvc
) -> SubscriptionResponse:
    """The plan this account is on.

    The plan shown is the one the account actually gets, which is not always
    the one the subscription names: a cancelled or unpaid subscription falls
    back to the free plan.
    """
    subscription = await svc.get_for(user.id)
    plan = await quota.plan_for(user.id)
    return SubscriptionResponse(
        tier=subscription.tier,
        status=subscription.status,
        period_start=subscription.period_start,
        period_end=subscription.period_end,
        plan=PlanResponse.of(plan),
    )


@router.get("/usage", response_model=UsageResponse)
async def my_usage(user: CurrentUser, quota: QuotaSvc) -> UsageResponse:
    """What this account has used this period, and what it is allowed."""
    allowance = await quota.allowance(user.id)
    plan = allowance.plan
    return UsageResponse(
        tier=plan.tier,
        plan=PlanResponse.of(plan),
        projects_used=allowance.projects_used,
        projects_limit=plan.max_projects,
        queries_used=allowance.queries_used,
        queries_limit=plan.monthly_queries,
        period_start=allowance.period_start,
        period_end=allowance.period_end,
    )
