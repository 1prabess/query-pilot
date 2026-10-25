"""What a user has used, and whether they may do the thing they are asking for.

The counting is done from the tables that already exist: a question answered
is a row in `queries`, a project is a row in `projects`. There is no counter
to keep in step with reality, which is the failure mode of every usage
table that has to be incremented in step with the work it counts -- the
first request that fails after the increment is a customer who paid for
nothing and cannot see why.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PlanLimitExceeded, ProjectLimitReached, QuotaExceeded
from app.models.project import Project
from app.models.query import Query
from app.models.subscription import Subscription
from app.services.plans import ACTIVE, FREE_PLAN, Plan, get_plan


@dataclass(frozen=True)
class Allowance:
    """What an account has used, and what its plan allows.

    One read of the world, so that a plan and the usage it is checked
    against cannot come from two different moments.
    """

    plan: Plan
    projects_used: int
    queries_used: int
    period_start: datetime
    period_end: datetime
    subscription: Subscription | None


class QuotaService:
    """Reads what a user has used, and refuses what they have not earned.

    The session arrives in the constructor and every method uses it. Nothing
    here commits; the caller owns the unit of work.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def subscription_for(self, user_id: uuid.UUID) -> Subscription | None:
        return await self.session.scalar(
            select(Subscription).where(Subscription.user_id == user_id)
        )

    async def plan_for(self, user_id: uuid.UUID) -> Plan:
        """The plan the user is on right now.

        A subscription that is not active -- cancelled, or a payment that did
        not go through -- gives the free plan. Losing the paid plan is a
        worse outcome than losing the account, and a customer whose card
        bounced is not a customer to lock out.
        """
        return _plan_for(await self.subscription_for(user_id))

    async def allowance(self, user_id: uuid.UUID) -> Allowance:
        """The plan, the period, and everything used against them."""
        subscription = await self.subscription_for(user_id)
        period_start, period_end = _period(subscription)
        return Allowance(
            plan=_plan_for(subscription),
            projects_used=await self._projects_used(user_id),
            queries_used=await self._queries_used(user_id, period_start, period_end),
            period_start=period_start,
            period_end=period_end,
            subscription=subscription,
        )

    def assert_can_create_project(self, allowance: Allowance) -> None:
        """Refuse a project the user's plan has no room for."""
        plan = allowance.plan
        if allowance.projects_used >= plan.max_projects:
            raise ProjectLimitReached(
                f"Your {plan.label} plan allows {plan.max_projects} "
                f"project{'s' if plan.max_projects != 1 else ''}. "
                "Upgrade the plan to add another."
            )

    def assert_can_ask(self, allowance: Allowance) -> None:
        """Refuse a question the user's plan has no room for.

        Called before the model is asked, so a customer who is out of
        allowance does not spend a token on an answer we are going to throw
        away.
        """
        plan = allowance.plan
        if plan.monthly_queries is None:
            return

        if allowance.queries_used >= plan.monthly_queries:
            raise QuotaExceeded(
                f"Your {plan.label} plan allows {plan.monthly_queries} questions "
                f"a month and you have used {allowance.queries_used}. The "
                f"allowance resets on {allowance.period_end:%-d %B %Y}."
            )

    def assert_rows_allowed(self, plan: Plan, limit: int) -> None:
        """Refuse a request for more rows than the plan allows.

        Refused rather than quietly trimmed: a caller that asked for a
        thousand rows and got a hundred should be told, because the answer
        they are looking at is not the answer they asked for.
        """
        if limit > plan.max_rows:
            raise PlanLimitExceeded(
                f"Your {plan.label} plan returns at most {plan.max_rows} rows "
                "per query. Upgrade the plan, or ask for fewer rows."
            )

    async def _projects_used(self, user_id: uuid.UUID) -> int:
        count = await self.session.scalar(
            select(func.count())
            .select_from(Project)
            .where(Project.owner_id == user_id)
        )
        return int(count or 0)

    async def _queries_used(
        self, user_id: uuid.UUID, start: datetime, end: datetime
    ) -> int:
        count = await self.session.scalar(
            select(func.count())
            .select_from(Query)
            .join(Project, Project.id == Query.project_id)
            .where(
                Project.owner_id == user_id,
                Query.created_at >= start,
                Query.created_at < end,
            )
        )
        return int(count or 0)


def _plan_for(subscription: Subscription | None) -> Plan:
    """The plan a subscription actually grants."""
    if subscription is None or subscription.status != ACTIVE:
        return FREE_PLAN
    return get_plan(subscription.tier)


def _period(subscription: Subscription | None) -> tuple[datetime, datetime]:
    """The window usage is counted over.

    A subscription with a period of its own is counted over that period --
    a payment provider's billing cycle, which need not be a calendar month.
    Everything else is counted over the calendar month.
    """
    now = datetime.now(UTC)
    if (
        subscription is not None
        and subscription.period_start is not None
        and subscription.period_end is not None
        and subscription.period_end > now
    ):
        return subscription.period_start, subscription.period_end

    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, end
