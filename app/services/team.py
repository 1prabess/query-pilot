from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import security
from app.core.config import get_settings
from app.core.errors import (
    AlreadyAMember,
    CannotRemoveOwner,
    InvalidName,
    InvalidRole,
    InvitationAlreadyAccepted,
    InvitationEmailMismatch,
    InvitationExpired,
    InvitationNotFound,
    PermissionDenied,
    TeamLimitReached,
    TeamNotFound,
)
from app.models.subscription import Subscription
from app.models.team import Team
from app.models.team_invitation import TeamInvitation
from app.models.team_membership import (
    ADMIN,
    MEMBER,
    OWNER,
    TEAM_ROLES,
    TeamMembership,
)
from app.models.user import User
from app.services import permissions
from app.services.plans import ACTIVE, Plan, best_plan
from app.services.quota import QuotaService

settings = get_settings()


@dataclass(frozen=True)
class TeamSummary:
    id: uuid.UUID
    name: str
    role: str
    can_write: bool
    created_at: datetime


class TeamService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.quota = QuotaService(session)

    async def create_team(self, owner: User, name: str) -> Team:
        cleaned = name.strip()
        if not cleaned:
            raise InvalidName("A team needs a name.")

        plan = await self.plan_for_account(owner.id)
        owned = await self._owned_count(owner.id)
        if owned >= plan.max_teams:
            raise TeamLimitReached(
                f"Your {plan.label} plan allows {plan.max_teams} team"
                f"{'s' if plan.max_teams != 1 else ''}. "
                "Upgrade the plan to create another."
            )

        team = Team(name=cleaned)
        self.session.add(team)
        await self.session.flush()

        self.session.add(
            TeamMembership(
                team_id=team.id,
                user_id=owner.id,
                role=OWNER,
                can_write=True,
            )
        )
        await self.session.flush()
        return team

    async def list_teams(self, user: User) -> list[TeamSummary]:
        rows = await self.session.execute(
            select(Team, TeamMembership)
            .join(TeamMembership, TeamMembership.team_id == Team.id)
            .where(TeamMembership.user_id == user.id)
            .order_by(Team.created_at)
        )
        return [
            TeamSummary(
                id=team.id,
                name=team.name,
                role=membership.role,
                can_write=membership.can_write,
                created_at=team.created_at,
            )
            for team, membership in rows.all()
        ]

    async def get_team(
        self, team_id: uuid.UUID, user_id: uuid.UUID
    ) -> tuple[Team, TeamMembership]:
        row = (
            await self.session.execute(
                select(Team, TeamMembership)
                .join(TeamMembership, TeamMembership.team_id == Team.id)
                .where(Team.id == team_id, TeamMembership.user_id == user_id)
            )
        ).first()
        if row is None:
            raise TeamNotFound("No such team.")
        return row

    async def membership(
        self, team_id: uuid.UUID, user_id: uuid.UUID
    ) -> TeamMembership:
        membership = await self.session.scalar(
            select(TeamMembership).where(
                TeamMembership.team_id == team_id,
                TeamMembership.user_id == user_id,
            )
        )
        if membership is None:
            raise TeamNotFound("No such team.")
        return membership

    async def default_team_id(self, user_id: uuid.UUID) -> uuid.UUID:
        """The team a request is about when it names none.

        The one the caller joined or made most recently, which is the one they
        are most likely working in. A new account has only the team it was
        given, so this is that one.
        """
        row = await self.session.scalar(
            select(TeamMembership.team_id)
            .where(TeamMembership.user_id == user_id)
            .order_by(TeamMembership.created_at.desc())
            .limit(1)
        )
        if row is None:
            raise TeamNotFound("You are not a member of any team.")
        return row

    async def resolve_membership(
        self, user_id: uuid.UUID, team_id: uuid.UUID | None
    ) -> TeamMembership:
        """The caller's membership of the team a request is about.

        A request that names no team is about the account's default team, so
        that a client which has not yet learned about teams still works.
        """
        if team_id is None:
            team_id = await self.default_team_id(user_id)
        return await self.membership(team_id, user_id)

    async def rename(self, actor: TeamMembership, name: str) -> Team:
        permissions.require(actor.role, permissions.MANAGE_TEAM)
        cleaned = name.strip()
        if not cleaned:
            raise InvalidName("A team needs a name.")
        team = await self.session.get(Team, actor.team_id)
        if team is None:
            raise TeamNotFound("No such team.")
        team.name = cleaned
        await self.session.flush()
        return team

    async def list_members(self, team_id: uuid.UUID) -> list[TeamMembership]:
        result = await self.session.scalars(
            select(TeamMembership)
            .options(selectinload(TeamMembership.user))
            .where(TeamMembership.team_id == team_id)
            .order_by(TeamMembership.created_at)
        )
        return list(result.all())

    async def invite(
        self,
        actor: TeamMembership,
        *,
        email: str,
        role: str,
        can_write: bool,
    ) -> tuple[TeamInvitation, str]:
        permissions.require(actor.role, permissions.MANAGE_MEMBERS)
        if role not in TEAM_ROLES or role == OWNER:
            raise InvalidRole(f"'{role}' is not a role a person can be invited as.")

        normalized = email.strip().lower()
        if not normalized:
            raise InvalidRole("An invitation needs an email address.")

        already = await self.session.scalar(
            select(TeamMembership)
            .join(User, User.id == TeamMembership.user_id)
            .where(
                TeamMembership.team_id == actor.team_id,
                User.email == normalized,
            )
        )
        if already is not None:
            raise AlreadyAMember("That person is already in this team.")

        raw = security.new_refresh_token()
        invitation = TeamInvitation(
            team_id=actor.team_id,
            email=normalized,
            role=role,
            can_write=can_write,
            token_hash=security.hash_token(raw),
            invited_by_id=actor.user_id,
            expires_at=datetime.now(UTC)
            + timedelta(hours=settings.INVITATION_TTL_HOURS),
        )
        self.session.add(invitation)
        await self.session.flush()
        return invitation, raw

    async def list_invitations(self, team_id: uuid.UUID) -> list[TeamInvitation]:
        result = await self.session.scalars(
            select(TeamInvitation)
            .where(
                TeamInvitation.team_id == team_id,
                TeamInvitation.accepted_at.is_(None),
            )
            .order_by(TeamInvitation.created_at.desc())
        )
        return list(result.all())

    async def revoke_invitation(
        self, actor: TeamMembership, invitation_id: uuid.UUID
    ) -> None:
        permissions.require(actor.role, permissions.MANAGE_MEMBERS)
        invitation = await self.session.get(TeamInvitation, invitation_id)
        if invitation is None or invitation.team_id != actor.team_id:
            raise InvitationNotFound("No such invitation.")
        await self.session.delete(invitation)
        await self.session.flush()

    async def invitation_by_token(
        self, raw_token: str
    ) -> tuple[TeamInvitation, Team]:
        """The invitation a token names, and the team it is for.

        The token is the secret. It is stored hashed, so it is compared
        against the hash and never read back.
        """
        row = (
            await self.session.execute(
                select(TeamInvitation, Team)
                .join(Team, Team.id == TeamInvitation.team_id)
                .where(
                    TeamInvitation.token_hash == security.hash_token(raw_token)
                )
            )
        ).first()
        if row is None:
            raise InvitationNotFound("This invitation is not valid.")
        return row[0], row[1]

    async def accept_invitation(self, user: User, raw_token: str) -> TeamMembership:
        invitation, _ = await self.invitation_by_token(raw_token)
        if invitation.accepted_at is not None:
            raise InvitationAlreadyAccepted(
                "This invitation has already been used."
            )
        if invitation.expires_at <= datetime.now(UTC):
            raise InvitationExpired(
                "This invitation has expired. Ask for a new one."
            )
        if invitation.email.lower() != user.email.lower():
            raise InvitationEmailMismatch(
                "This invitation was sent to a different email address."
            )

        membership = await self.session.scalar(
            select(TeamMembership).where(
                TeamMembership.team_id == invitation.team_id,
                TeamMembership.user_id == user.id,
            )
        )
        if membership is None:
            membership = TeamMembership(
                team_id=invitation.team_id,
                user_id=user.id,
                role=invitation.role,
                can_write=invitation.can_write,
            )
            self.session.add(membership)

        invitation.accepted_at = datetime.now(UTC)
        await self.session.flush()
        return membership

    async def set_role(
        self, actor: TeamMembership, target_user_id: uuid.UUID, role: str
    ) -> TeamMembership:
        permissions.require(actor.role, permissions.MANAGE_MEMBERS)
        if role not in TEAM_ROLES or role == OWNER:
            raise InvalidRole(f"'{role}' is not a role a person can be given.")

        target = await self._target(actor, target_user_id)
        self._assert_outranks(actor, target)

        if target.role == OWNER:
            raise PermissionDenied(
                "The team owner cannot step down. Transfer ownership first."
            )

        target.role = role
        await self.session.flush()
        return target

    async def set_write_access(
        self, actor: TeamMembership, target_user_id: uuid.UUID, can_write: bool
    ) -> TeamMembership:
        permissions.require(actor.role, permissions.MANAGE_MEMBERS)
        target = await self._target(actor, target_user_id)
        self._assert_outranks(actor, target)

        if target.role == OWNER:
            raise PermissionDenied(
                "The team owner always has write access."
            )

        target.can_write = can_write
        await self.session.flush()
        return target

    async def remove_member(
        self, actor: TeamMembership, target_user_id: uuid.UUID
    ) -> None:
        permissions.require(actor.role, permissions.MANAGE_MEMBERS)
        target = await self._target(actor, target_user_id)
        self._assert_outranks(actor, target)

        if target.role == OWNER:
            raise CannotRemoveOwner(
                "The team owner cannot be removed. Transfer ownership first."
            )

        await self.session.delete(target)
        await self.session.flush()

    async def transfer_ownership(
        self, actor: TeamMembership, target_user_id: uuid.UUID
    ) -> TeamMembership:
        if actor.role != OWNER:
            raise PermissionDenied(
                "Only the team owner can transfer ownership."
            )

        target = await self._target(actor, target_user_id)
        target.role = OWNER
        target.can_write = True
        actor.role = ADMIN
        await self.session.flush()
        return target

    async def plan_for_account(self, user_id: uuid.UUID) -> Plan:
        """The plan that governs what this account may create.

        An account can own several teams and each team has its own plan. The
        limit on how many teams an account may own comes from the best of
        them: paying for one of its teams is paying for the account.
        """
        tiers = await self.session.scalars(
            select(Subscription.tier)
            .join(
                TeamMembership,
                TeamMembership.team_id == Subscription.team_id,
            )
            .where(
                TeamMembership.user_id == user_id,
                TeamMembership.role == OWNER,
                Subscription.status == ACTIVE,
            )
        )
        return best_plan(tiers)

    async def _owned_count(self, user_id: uuid.UUID) -> int:
        count = await self.session.scalar(
            select(func.count())
            .select_from(TeamMembership)
            .where(
                TeamMembership.user_id == user_id,
                TeamMembership.role == OWNER,
            )
        )
        return int(count or 0)

    async def _target(
        self, actor: TeamMembership, target_user_id: uuid.UUID
    ) -> TeamMembership:
        target = await self.session.scalar(
            select(TeamMembership)
            .options(selectinload(TeamMembership.user))
            .where(
                TeamMembership.team_id == actor.team_id,
                TeamMembership.user_id == target_user_id,
            )
        )
        if target is None:
            raise InvitationNotFound("That person is not in this team.")
        return target

    def _assert_outranks(
        self, actor: TeamMembership, target: TeamMembership
    ) -> None:
        if actor.role == OWNER:
            return
        if target.role == OWNER:
            raise PermissionDenied(
                "Only the team owner can change the owner's access."
            )
        if target.role == ADMIN:
            raise PermissionDenied(
                "Only the team owner can change another admin's access."
            )


__all__ = ["ADMIN", "MEMBER", "OWNER", "TeamService", "TeamSummary"]
