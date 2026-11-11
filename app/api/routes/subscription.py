from uuid import UUID

from fastapi import APIRouter

from app.api.deps import CurrentUser, QuotaSvc, SubscriptionSvc, TeamSvc
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
    user: CurrentUser,
    svc: SubscriptionSvc,
    quota: QuotaSvc,
    teams: TeamSvc,
    team_id: UUID | None = None,
) -> SubscriptionResponse:
    """The plan this team is on.

    A plan belongs to a team, so the answer is about one of them. Without a
    team named, it is the caller's default team.

    The plan shown is the one the team actually gets, which is not always the
    one the subscription names: a cancelled or unpaid subscription falls back
    to the free plan.
    """
    membership = await teams.resolve_membership(user.id, team_id)
    subscription = await svc.get_for(membership.team_id)
    plan = await quota.plan_for_team(membership.team_id)
    return SubscriptionResponse(
        tier=subscription.tier,
        status=subscription.status,
        period_start=subscription.period_start,
        period_end=subscription.period_end,
        plan=PlanResponse.of(plan),
    )


@router.get("/usage", response_model=UsageResponse)
async def my_usage(
    user: CurrentUser, quota: QuotaSvc, teams: TeamSvc, team_id: UUID | None = None
) -> UsageResponse:
    """What this team has used this period, and what it is allowed."""
    membership = await teams.resolve_membership(user.id, team_id)
    return UsageResponse.of(
        await quota.allowance_for_team(membership.team_id)
    )
