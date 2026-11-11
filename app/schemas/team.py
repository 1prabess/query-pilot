"""What a team looks like from outside."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class CreateTeamRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class RenameTeamRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class TeamSummaryResponse(BaseModel):
    """A team, as the caller's own list shows it.

    The role and the write grant are the caller's, not the team's: two people
    in one team see different things about it.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    role: str
    can_write: bool
    created_at: datetime


class TeamResponse(BaseModel):
    """One team, seen by one member."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    created_at: datetime
    updated_at: datetime
    role: str
    can_write: bool


class MemberResponse(BaseModel):
    """One person in a team."""

    user_id: UUID
    email: str
    role: str
    can_write: bool
    created_at: datetime


class UpdateMemberRequest(BaseModel):
    """A change to one member's access.

    Both fields are optional, so a request may change the role, the write
    grant, or both.
    """

    role: str | None = Field(default=None, min_length=1, max_length=20)
    can_write: bool | None = None


class InviteRequest(BaseModel):
    email: EmailStr
    role: str = Field(default="member", min_length=1, max_length=20)
    can_write: bool = False


class InvitationResponse(BaseModel):
    """An invitation, as the team list shows it."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    role: str
    can_write: bool
    invited_by_id: UUID | None
    expires_at: datetime
    created_at: datetime


class CreatedInvitationResponse(InvitationResponse):
    """A new invitation, with the token that accepts it.

    The token is in the answer and nowhere else: it is not stored, so it can
    only be read here, and whoever is given this response is whoever will
    pass the link on.
    """

    token: str


class InvitationPreviewResponse(BaseModel):
    """What an invited person is being invited to."""

    team_name: str
    email: str
    role: str
    can_write: bool
    expires_at: datetime


class TransferOwnershipRequest(BaseModel):
    user_id: UUID
