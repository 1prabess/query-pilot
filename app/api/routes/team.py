from uuid import UUID

from fastapi import APIRouter, Response, status

from app.api.deps import CurrentUser, DbSession, TeamSvc
from app.models.team_membership import TeamMembership
from app.schemas.team import (
    CreatedInvitationResponse,
    CreateTeamRequest,
    InvitationPreviewResponse,
    InvitationResponse,
    InviteRequest,
    MemberResponse,
    RenameTeamRequest,
    TeamResponse,
    TeamSummaryResponse,
    TransferOwnershipRequest,
    UpdateMemberRequest,
)

router = APIRouter(prefix="/teams", tags=["teams"])
invitations_router = APIRouter(prefix="/team-invitations", tags=["teams"])


@router.post("", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
async def create_team(
    body: CreateTeamRequest, session: DbSession, svc: TeamSvc, user: CurrentUser
) -> TeamResponse:
    """A new team, with the caller as its owner."""
    team = await svc.create_team(user, body.name)
    await session.commit()
    return _team_response(team, role="owner", can_write=True)


@router.get("", response_model=list[TeamSummaryResponse])
async def list_teams(svc: TeamSvc, user: CurrentUser) -> list[TeamSummaryResponse]:
    """Every team the caller is in."""
    return [
        TeamSummaryResponse.model_validate(summary)
        for summary in await svc.list_teams(user)
    ]


@router.get("/{team_id}", response_model=TeamResponse)
async def get_team(
    team_id: UUID, svc: TeamSvc, user: CurrentUser
) -> TeamResponse:
    """One team the caller is in."""
    team, membership = await svc.get_team(team_id, user.id)
    return _team_response(
        team, role=membership.role, can_write=membership.can_write
    )


@router.patch("/{team_id}", response_model=TeamResponse)
async def rename_team(
    team_id: UUID,
    body: RenameTeamRequest,
    session: DbSession,
    svc: TeamSvc,
    user: CurrentUser,
) -> TeamResponse:
    """Give the team a different name."""
    _, membership = await svc.get_team(team_id, user.id)
    team = await svc.rename(membership, body.name)
    await session.commit()
    return _team_response(
        team, role=membership.role, can_write=membership.can_write
    )


@router.get("/{team_id}/members", response_model=list[MemberResponse])
async def list_members(
    team_id: UUID, svc: TeamSvc, user: CurrentUser
) -> list[MemberResponse]:
    """Everyone in the team, and what each of them may do."""
    await svc.get_team(team_id, user.id)
    members = await svc.list_members(team_id)
    return [_member_response(member) for member in members]


@router.patch("/{team_id}/members/{user_id}", response_model=MemberResponse)
async def update_member(
    team_id: UUID,
    user_id: UUID,
    body: UpdateMemberRequest,
    session: DbSession,
    svc: TeamSvc,
    user: CurrentUser,
) -> MemberResponse:
    """Change what one member may do.

    The role says what a person may administer; the write grant says whether
    they may change the customer's data. They are separate, so a database
    manager can be given the first without the second.
    """
    _, actor = await svc.get_team(team_id, user.id)

    membership = actor
    if body.role is not None:
        membership = await svc.set_role(actor, user_id, body.role)
    if body.can_write is not None:
        membership = await svc.set_write_access(
            actor, user_id, body.can_write
        )
    await session.commit()
    return _member_response(membership)


@router.delete(
    "/{team_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_member(
    team_id: UUID,
    user_id: UUID,
    session: DbSession,
    svc: TeamSvc,
    user: CurrentUser,
) -> Response:
    """Take someone out of the team."""
    _, actor = await svc.get_team(team_id, user.id)
    await svc.remove_member(actor, user_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{team_id}/invitations",
    response_model=CreatedInvitationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def invite_member(
    team_id: UUID,
    body: InviteRequest,
    session: DbSession,
    svc: TeamSvc,
    user: CurrentUser,
) -> CreatedInvitationResponse:
    """Invite somebody into the team.

    The token is in the answer and nowhere else. It is not stored, so it can
    only be read here, and whoever gets this response is whoever passes the
    link on.
    """
    _, actor = await svc.get_team(team_id, user.id)
    invitation, raw = await svc.invite(
        actor,
        email=body.email,
        role=body.role,
        can_write=body.can_write,
    )
    await session.commit()
    return CreatedInvitationResponse(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        can_write=invitation.can_write,
        invited_by_id=invitation.invited_by_id,
        expires_at=invitation.expires_at,
        created_at=invitation.created_at,
        token=raw,
    )


@router.get(
    "/{team_id}/invitations", response_model=list[InvitationResponse]
)
async def list_invitations(
    team_id: UUID, svc: TeamSvc, user: CurrentUser
) -> list[InvitationResponse]:
    """The invitations this team has out, and which have been taken up."""
    await svc.get_team(team_id, user.id)
    invitations = await svc.list_invitations(team_id)
    return [
        InvitationResponse.model_validate(invitation)
        for invitation in invitations
    ]


@router.delete(
    "/{team_id}/invitations/{invitation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_invitation(
    team_id: UUID,
    invitation_id: UUID,
    session: DbSession,
    svc: TeamSvc,
    user: CurrentUser,
) -> Response:
    """Call an invitation back."""
    _, actor = await svc.get_team(team_id, user.id)
    await svc.revoke_invitation(actor, invitation_id)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{team_id}/transfer", response_model=MemberResponse)
async def transfer_ownership(
    team_id: UUID,
    body: TransferOwnershipRequest,
    session: DbSession,
    svc: TeamSvc,
    user: CurrentUser,
) -> MemberResponse:
    """Hand the team over to somebody else.

    The owner becomes an admin. A team has one owner, and the only way to
    change who that is is to give it away.
    """
    _, actor = await svc.get_team(team_id, user.id)
    membership = await svc.transfer_ownership(actor, body.user_id)
    await session.commit()
    return _member_response(membership)


@invitations_router.get("/{token}", response_model=InvitationPreviewResponse)
async def preview_invitation(
    token: str, svc: TeamSvc
) -> InvitationPreviewResponse:
    """What an invited person is being invited to.

    No sign-in: the person holding the link may not have an account yet.
    """
    invitation, team = await svc.invitation_by_token(token)
    return InvitationPreviewResponse(
        team_name=team.name,
        email=invitation.email,
        role=invitation.role,
        can_write=invitation.can_write,
        expires_at=invitation.expires_at,
    )


@invitations_router.post("/{token}/accept", response_model=MemberResponse)
async def accept_invitation(
    token: str, session: DbSession, svc: TeamSvc, user: CurrentUser
) -> MemberResponse:
    """Take the invitation up.

    The invitation was sent to one address, and only that address may take
    it up.
    """
    membership = await svc.accept_invitation(user, token)
    await session.commit()
    return MemberResponse(
        user_id=membership.user_id,
        email=user.email,
        role=membership.role,
        can_write=membership.can_write,
        created_at=membership.created_at,
    )


def _team_response(team, *, role: str, can_write: bool) -> TeamResponse:
    return TeamResponse(
        id=team.id,
        name=team.name,
        created_at=team.created_at,
        updated_at=team.updated_at,
        role=role,
        can_write=can_write,
    )


def _member_response(membership: TeamMembership) -> MemberResponse:
    return MemberResponse(
        user_id=membership.user_id,
        email=membership.user.email,
        role=membership.role,
        can_write=membership.can_write,
        created_at=membership.created_at,
    )
