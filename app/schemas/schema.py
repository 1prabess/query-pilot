from typing import Literal

from pydantic import BaseModel


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
