"""What an administrator is shown about an account."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.subscription import UsageResponse


class AdminUserSummary(BaseModel):
    """One row of the account list.

    The counts are totals -- projects ever made, questions ever asked -- not
    this period's usage, because the period belongs to each subscription.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    role: str
    is_active: bool
    email_verified: bool
    created_at: datetime
    tier: str
    status: str
    period_start: datetime | None
    period_end: datetime | None
    project_count: int
    query_count: int
    last_query_at: datetime | None


class AdminUserListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    users: list[AdminUserSummary]


class AdminUserDetail(AdminUserSummary):
    """One account, with the usage counted over its own billing period.

    `usage` is what the account is actually allowed right now. It can differ
    from the tier above it: a premium subscription that has been cancelled
    still says premium, and still gets the free plan.
    """

    usage: UsageResponse
    provider_customer_id: str | None
    provider_subscription_id: str | None
