from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ColumnInfo(BaseModel):
    """One column, as the schema document describes it."""

    name: str
    type: str
    nullable: bool
    primary_key: bool


class ForeignKeyInfo(BaseModel):
    """A foreign key. `on_delete` is absent when the rule is NO ACTION."""

    columns: list[str]
    references: str
    on_delete: str | None = None


class TableInfo(BaseModel):
    name: str
    columns: list[ColumnInfo]
    primary_key: list[str]
    unique: list[list[str]]
    foreign_keys: list[ForeignKeyInfo]


class SchemaDocument(BaseModel):
    """The shape of a project's schema, whichever way it was read.

    `source` says how it was obtained, and it is part of the document because a
    dump and a live introspection are the same facts read two ways: the tables
    are identical, only the provenance differs.
    """

    source: Literal["dump", "introspection"]
    table_count: int
    truncated: bool
    tables: list[TableInfo]


class SchemaVersionResponse(BaseModel):
    """One state of a project's schema, and when we found it."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    version: int
    captured_at: datetime
    captured_by: UUID | None = None
    source: str
    change_count: int


class SchemaVersionListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    versions: list[SchemaVersionResponse]


class SchemaChangeResponse(BaseModel):
    """One change to a project's schema.

    `summary` says the change in a sentence, so a client can put it on
    screen without knowing what each `kind` means.

    `affected_queries` are the saved questions that named the column this
    change touched, and would now fail.
    """

    id: UUID
    version: int
    kind: str
    severity: str
    summary: str
    table_name: str
    column_name: str | None = None
    detail: str | None = None
    affected_queries: list[UUID] = []
    created_at: datetime


class SchemaChangeListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    changes: list[SchemaChangeResponse]
