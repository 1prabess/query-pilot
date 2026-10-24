from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ProjectResponse(BaseModel):
    """A project as the API exposes it. Never the credential."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    writes_enabled: bool
    created_at: datetime
    updated_at: datetime


class SetConnectionRequest(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    database: str = Field(min_length=1, max_length=255)
    user: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)
    schema_name: str = Field(default="public", min_length=1, max_length=63)


class ConnectionResponse(BaseModel):
    """The stored connection. The password is not part of it."""

    host: str
    port: int
    database: str
    user: str
    schema_name: str


class SetWritesEnabledRequest(BaseModel):
    enabled: bool
