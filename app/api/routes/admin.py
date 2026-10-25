from uuid import UUID

from fastapi import APIRouter

from app.api.deps import AdminUser, DbSession, SubscriptionSvc
from app.core.errors import UserNotFound
from app.models.user import User
from app.schemas.subscription import (
    PlanResponse,
    SetPlanRequest,
    SubscriptionResponse,
)
from app.services.plans import get_plan

router = APIRouter(prefix="/admin", tags=["admin"])


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
