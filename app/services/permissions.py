from __future__ import annotations

from app.core.errors import PermissionDenied
from app.models.team_membership import ADMIN, MEMBER, OWNER, TeamMembership

MANAGE_MEMBERS = "manage_members"
MANAGE_TEAM = "manage_team"
DELETE_TEAM = "delete_team"
MANAGE_PLAN = "manage_plan"
MANAGE_DATABASES = "manage_databases"
ASK = "ask"
RUN = "run"
WRITE = "write"

_ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    OWNER: frozenset(
        {
            MANAGE_MEMBERS,
            MANAGE_TEAM,
            DELETE_TEAM,
            MANAGE_PLAN,
            MANAGE_DATABASES,
            ASK,
            RUN,
            WRITE,
        }
    ),
    ADMIN: frozenset(
        {MANAGE_MEMBERS, MANAGE_TEAM, MANAGE_DATABASES, ASK, RUN}
    ),
    MEMBER: frozenset({ASK, RUN}),
}


def can(role: str, capability: str) -> bool:
    return capability in _ROLE_CAPABILITIES.get(role, frozenset())


def require(role: str, capability: str) -> None:
    """Refuse a capability the role does not have.

    The refusal is the same wherever it is asked for, so that a service
    cannot answer the question differently from another one.
    """
    if not can(role, capability):
        raise PermissionDenied("Your role in this team does not allow that.")


def may_write(membership: TeamMembership) -> bool:
    return membership.role == OWNER or membership.can_write
