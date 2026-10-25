import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, Select, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.query import Query
from app.models.subscription import Subscription
from app.models.user import User
from app.services.plans import ACTIVE, FREE
from app.services.quota import Allowance, QuotaService


@dataclass(frozen=True)
class UserSummary:
    """One account, as the list shows it."""

    id: uuid.UUID
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


@dataclass(frozen=True)
class UserDetail:
    """One account, with the usage counted over its own billing period."""

    summary: UserSummary
    provider_customer_id: str | None
    provider_subscription_id: str | None
    allowance: Allowance


class AdminService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.quota = QuotaService(session)

    async def list_users(
        self, *, limit: int, offset: int
    ) -> tuple[list[UserSummary], int]:
        """Every account, with the plan it is on and what it has used.

        The counts are totals, not this period's usage. The period belongs to
        each subscription, so the number that depends on it is on the
        account's own page instead of here.
        """
        rows = await self.session.execute(
            _summary_query().limit(limit).offset(offset)
        )
        total = await self.session.scalar(select(func.count()).select_from(User))
        return [_summary_from(row) for row in rows], int(total or 0)

    async def get_user(self, user_id: uuid.UUID) -> UserDetail | None:
        """One account, and everything counted against it."""
        rows = await self.session.execute(
            _summary_query().where(User.id == user_id)
        )
        row = rows.first()
        if row is None:
            return None

        allowance = await self.quota.allowance(user_id)
        subscription = allowance.subscription
        return UserDetail(
            summary=_summary_from(row),
            provider_customer_id=(
                subscription.provider_customer_id if subscription else None
            ),
            provider_subscription_id=(
                subscription.provider_subscription_id if subscription else None
            ),
            allowance=allowance,
        )


# The tail is `Any` because the checker gives up naming column types past
# nine of them -- `select()` of ten columns already infers `*tuple[Any, ...]`.
# The three counts are integers and a timestamp all the same.
def _summary_query() -> Select[
    uuid.UUID,
    str,
    str,
    bool,
    bool,
    datetime,
    str,
    str,
    datetime | None,
    datetime | None,
    *tuple[Any, ...],
]:
    """Every account, joined to its subscription and its usage.

    Grouped once, so the row count does not grow with the account count.
    The projects and the queries are joined to the same account and to each
    other, which means one query row is one query row -- a user with two
    projects and five questions produces five joined rows, and the distinct
    count on the project side still says two.
    """
    return (
        select(
            User.id,
            User.email,
            User.role,
            User.is_active,
            User.email_verified,
            User.created_at,
            Subscription.tier,
            Subscription.status,
            Subscription.period_start,
            Subscription.period_end,
            func.count(distinct(Project.id)).label("project_count"),
            func.count(Query.id).label("query_count"),
            func.max(Query.created_at).label("last_query_at"),
        )
        .select_from(User)
        .outerjoin(Subscription, Subscription.user_id == User.id)
        .outerjoin(Project, Project.owner_id == User.id)
        .outerjoin(Query, Query.project_id == Project.id)
        .group_by(
            User.id,
            Subscription.tier,
            Subscription.status,
            Subscription.period_start,
            Subscription.period_end,
        )
        .order_by(User.created_at.desc())
    )


def _summary_from(
    row: Row[
        uuid.UUID,
        str,
        str,
        bool,
        bool,
        datetime,
        str,
        str,
        datetime | None,
        datetime | None,
        *tuple[Any, ...],
    ],
) -> UserSummary:
    """One grouped row as the summary the rest of the app reads.

    An account with no subscription row yet is on the free plan, which is
    what it would be given the moment anybody asked.
    """
    return UserSummary(
        id=row.id,
        email=row.email,
        role=row.role,
        is_active=row.is_active,
        email_verified=row.email_verified,
        created_at=row.created_at,
        tier=row.tier or FREE,
        status=row.status or ACTIVE,
        period_start=row.period_start,
        period_end=row.period_end,
        project_count=int(row.project_count or 0),
        query_count=int(row.query_count or 0),
        last_query_at=row.last_query_at,
    )
