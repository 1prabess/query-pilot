import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Row, Select, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.query import Query
from app.models.subscription import Subscription
from app.models.team import Team
from app.models.team_membership import OWNER, TeamMembership
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
    revoked_at: datetime | None
    revoked_by: uuid.UUID | None
    revoked_reason: str | None
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


@dataclass(frozen=True)
class TeamSummary:
    """One team, as the list shows it."""

    id: uuid.UUID
    name: str
    created_at: datetime
    member_count: int
    tier: str
    status: str
    project_count: int
    query_count: int


@dataclass(frozen=True)
class TeamDetail:
    """One team, with the usage counted over its own billing period."""

    summary: TeamSummary
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

        team_id = await self._first_owned_team_id(user_id)
        allowance = await self.quota.allowance_for_team(team_id)
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

    async def list_teams(
        self, *, limit: int, offset: int
    ) -> tuple[list[TeamSummary], int]:
        """Every team, the plan it is on, and what it has used."""
        rows = await self.session.execute(
            _team_query().limit(limit).offset(offset)
        )
        total = await self.session.scalar(select(func.count()).select_from(Team))
        return [_team_from(row) for row in rows], int(total or 0)

    async def get_team(self, team_id: uuid.UUID) -> TeamDetail | None:
        """One team, and everything counted against it."""
        rows = await self.session.execute(_team_query().where(Team.id == team_id))
        row = rows.first()
        if row is None:
            return None

        allowance = await self.quota.allowance_for_team(team_id)
        subscription = allowance.subscription
        return TeamDetail(
            summary=_team_from(row),
            provider_customer_id=(
                subscription.provider_customer_id if subscription else None
            ),
            provider_subscription_id=(
                subscription.provider_subscription_id if subscription else None
            ),
            allowance=allowance,
        )

    async def set_revoked(
        self,
        user_id: uuid.UUID,
        *,
        revoked: bool,
        reason: str | None = None,
        revoked_by: uuid.UUID | None = None,
    ) -> UserDetail | None:
        """Ban an account, or lift the ban.

        Revoking stamps the audit record -- when, who, why -- and deactivates
        the account in the same transaction. Lifting the ban clears the
        record.
        """
        user = await self.session.get(User, user_id)
        if user is None:
            return None

        already_revoked = not user.is_user_active
        if revoked == already_revoked:
            # Already in the state asked for: nothing to stamp.
            return await self.get_user(user_id)

        if revoked:
            user.revoked_at = datetime.now(UTC)
            user.revoked_by = revoked_by
            user.revoked_reason = reason
            user.is_active = False
        else:
            user.revoked_at = None
            user.revoked_by = None
            user.revoked_reason = None
            user.is_active = True

        await self.session.flush()
        return await self.get_user(user_id)

    async def _first_owned_team_id(
        self, user_id: uuid.UUID
    ) -> uuid.UUID | None:
        return await self.session.scalar(
            select(TeamMembership.team_id)
            .where(
                TeamMembership.user_id == user_id,
                TeamMembership.role == OWNER,
            )
            .order_by(TeamMembership.created_at)
            .limit(1)
        )


def _owned_team_ids() -> Select:
    """The teams one account owns.

    A fresh statement each time: the same select cannot be correlated into
    two different places in the same query.
    """
    return (
        select(TeamMembership.team_id)
        .where(
            TeamMembership.user_id == User.id,
            TeamMembership.role == OWNER,
        )
        .correlate(User)
    )


# The tail is `Any` because the checker gives up naming column types past
# nine of them -- `select()` of ten columns already infers `*tuple[Any, ...]`.
# The three counts are integers and a timestamp all the same.
def _summary_query() -> Select[
    uuid.UUID,
    str,
    str,
    bool,
    datetime | None,
    uuid.UUID | None,
    str | None,
    bool,
    datetime,
    str,
    str,
    datetime | None,
    datetime | None,
    *tuple[Any, ...],
]:
    """Every account, with the plan of the team it owns and what it has used.

    The counts are correlated subqueries rather than joins. An account that
    owns three teams would join three times over, and the row count would
    grow with the number of teams the account has.
    """
    project_count = (
        select(func.count())
        .select_from(Project)
        .where(Project.team_id.in_(_owned_team_ids()))
        .correlate(User)
        .scalar_subquery()
    )
    query_count = (
        select(func.count())
        .select_from(Query)
        .join(Project, Project.id == Query.project_id)
        .where(Project.team_id.in_(_owned_team_ids()))
        .correlate(User)
        .scalar_subquery()
    )
    last_query_at = (
        select(func.max(Query.created_at))
        .join(Project, Project.id == Query.project_id)
        .where(Project.team_id.in_(_owned_team_ids()))
        .correlate(User)
        .scalar_subquery()
    )
    first_owned_team = (
        select(TeamMembership.team_id)
        .where(
            TeamMembership.user_id == User.id,
            TeamMembership.role == OWNER,
        )
        .order_by(TeamMembership.created_at)
        .limit(1)
        .correlate(User)
        .scalar_subquery()
    )

    return (
        select(
            User.id,
            User.email,
            User.role,
            User.is_active,
            User.revoked_at,
            User.revoked_by,
            User.revoked_reason,
            User.email_verified,
            User.created_at,
            Subscription.tier,
            Subscription.status,
            Subscription.period_start,
            Subscription.period_end,
            project_count.label("project_count"),
            query_count.label("query_count"),
            last_query_at.label("last_query_at"),
        )
        .select_from(User)
        .outerjoin(Team, Team.id == first_owned_team)
        .outerjoin(Subscription, Subscription.team_id == Team.id)
        .order_by(User.created_at.desc())
    )


def _team_query() -> Select[
    uuid.UUID,
    str,
    datetime,
    int,
    str | None,
    str | None,
    int,
    int,
]:
    """Every team, with the plan it is on and what it has used."""
    return (
        select(
            Team.id,
            Team.name,
            Team.created_at,
            func.count(distinct(TeamMembership.user_id)).label("member_count"),
            Subscription.tier,
            Subscription.status,
            func.count(distinct(Project.id)).label("project_count"),
            func.count(distinct(Query.id)).label("query_count"),
        )
        .select_from(Team)
        .outerjoin(Subscription, Subscription.team_id == Team.id)
        .outerjoin(TeamMembership, TeamMembership.team_id == Team.id)
        .outerjoin(Project, Project.team_id == Team.id)
        .outerjoin(Query, Query.project_id == Project.id)
        .group_by(Team.id, Subscription.tier, Subscription.status)
        .order_by(Team.created_at.desc())
    )


def _summary_from(
    row: Row[
        uuid.UUID,
        str,
        str,
        bool,
        datetime | None,
        uuid.UUID | None,
        str | None,
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

    An account with no team of its own yet is on the free plan, which is
    what it would be given the moment anybody asked.
    """
    return UserSummary(
        id=row.id,
        email=row.email,
        role=row.role,
        is_active=row.is_active,
        revoked_at=row.revoked_at,
        revoked_by=row.revoked_by,
        revoked_reason=row.revoked_reason,
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


def _team_from(
    row: Row[
        uuid.UUID,
        str,
        datetime,
        int,
        str | None,
        str | None,
        int,
        int,
    ],
) -> TeamSummary:
    """One grouped row as the team summary the admin list shows."""
    return TeamSummary(
        id=row.id,
        name=row.name,
        created_at=row.created_at,
        member_count=int(row.member_count or 0),
        tier=row.tier or FREE,
        status=row.status or ACTIVE,
        project_count=int(row.project_count or 0),
        query_count=int(row.query_count or 0),
    )
