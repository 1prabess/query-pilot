from datetime import datetime

from pydantic import BaseModel, Field

from app.services.plans import Plan
from app.services.quota import Allowance


class PlanResponse(BaseModel):
    """What one tier allows.

    `monthly_queries` is null for an unlimited plan, so a client can tell
    "no limit" apart from "a limit of zero" without asking.
    """

    tier: str
    label: str
    max_projects: int
    monthly_queries: int | None
    max_rows: int
    execution_timeout_ms: int
    allows_writes: bool

    @classmethod
    def of(cls, plan: Plan) -> "PlanResponse":
        return cls(
            tier=plan.tier,
            label=plan.label,
            max_projects=plan.max_projects,
            monthly_queries=plan.monthly_queries,
            max_rows=plan.max_rows,
            execution_timeout_ms=plan.execution_timeout_ms,
            allows_writes=plan.allows_writes,
        )


class SubscriptionResponse(BaseModel):
    """The plan a user is on, and the state of that subscription."""

    tier: str
    status: str
    period_start: datetime | None
    period_end: datetime | None
    plan: PlanResponse


class UsageResponse(BaseModel):
    """What a user has used this period."""

    tier: str
    plan: PlanResponse
    projects_used: int
    projects_limit: int
    queries_used: int
    queries_limit: int | None
    period_start: datetime
    period_end: datetime

    @classmethod
    def of(cls, allowance: Allowance) -> "UsageResponse":
        """The usage as it is, without remapping it by hand.

        Two places show usage -- the account's own page and the
        administrator's page for it -- and they have to agree.
        """
        plan = allowance.plan
        return cls(
            tier=plan.tier,
            plan=PlanResponse.of(plan),
            projects_used=allowance.projects_used,
            projects_limit=plan.max_projects,
            queries_used=allowance.queries_used,
            queries_limit=plan.monthly_queries,
            period_start=allowance.period_start,
            period_end=allowance.period_end,
        )


class SetPlanRequest(BaseModel):
    """Put a user on a plan.

    The period is optional: leave it empty and usage is counted over the
    calendar month, which is what the free plan uses.
    """

    tier: str = Field(min_length=1, max_length=20)
    status: str = Field(default="active", min_length=1, max_length=20)
    period_start: datetime | None = None
    period_end: datetime | None = None
