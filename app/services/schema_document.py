"""The schema document, and the one place it is shaped.

A project's schema can be read two ways: parsed out of a `pg_dump` file, or
asked of a live database. Both answers describe the same database, so they have
to come out as the same document -- otherwise every consumer downstream has to
know which source a document came from before it can trust it.

That is why this module exists. The two readers produce their own facts, in
their own shapes, and hand them over as `RawTable` objects. Everything that
decides what the document *looks* -- the order of the tables, the spelling of
a type, whether `NO ACTION` is present or missing -- happens here, once.

No database, no SQL, no sqlglot: the two readers are the only things that
should know how a schema is stored in Postgres or how a dump is written.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_TABLES = 500

SOURCE_DUMP = "dump"
SOURCE_INTROSPECTION = "introspection"

# The names PostgreSQL itself prints in `format_type`, keyed by the other
# spellings the same type turns up under.
_TYPE_NAMES = {
    "int": "integer",
    "int4": "integer",
    "integer": "integer",
    "int2": "smallint",
    "smallint": "smallint",
    "int8": "bigint",
    "bigint": "bigint",
    "bool": "boolean",
    "boolean": "boolean",
    "varchar": "character varying",
    "character varying": "character varying",
    "char": "character",
    "character": "character",
    "bpchar": "character",
    "text": "text",
    "timestamptz": "timestamp with time zone",
    "timestamp with time zone": "timestamp with time zone",
    "timestamp without time zone": "timestamp without time zone",
    "timestamp": "timestamp without time zone",
    "timetz": "time with time zone",
    "time with time zone": "time with time zone",
    "time without time zone": "time without time zone",
    "time": "time without time zone",
    "float8": "double precision",
    "double precision": "double precision",
    "float4": "real",
    "real": "real",
    "float": "double precision",
    "decimal": "numeric",
    "numeric": "numeric",
    "varbinary": "bytea",
    "bytea": "bytea",
    "binary": "bytea",
    "json": "json",
    "jsonb": "jsonb",
    "uuid": "uuid",
    "date": "date",
    "money": "money",
    "inet": "inet",
    "cidr": "cidr",
    "macaddr": "macaddr",
    "interval": "interval",
}


@dataclass
class RawColumn:
    name: str
    type: str
    nullable: bool


@dataclass
class RawForeignKey:
    columns: list[str]
    references: str
    on_delete: str | None = None


@dataclass
class RawTable:
    name: str
    columns: list[RawColumn] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    unique: list[list[str]] = field(default_factory=list)
    foreign_keys: list[RawForeignKey] = field(default_factory=list)


def canonical_type(name: str) -> str:
    """Fold every spelling of a type into the one PostgreSQL prints.

    A dump says `timestamptz` where `format_type` says `timestamp with time
    zone`, and a dump says `varchar(255)` where `format_type` says
    `character varying(255)`. Those are the same type, so they have to end up
    as the same string or the two readers disagree about a table that is
    perfectly ordinary.
    """
    text = name.strip().lower()
    if not text:
        return text

    if text.endswith("[]"):
        return f"{canonical_type(text[:-2])}[]"

    qualifier = ""
    for suffix in (" without time zone", " with time zone"):
        if text.endswith(suffix):
            qualifier = suffix
            text = text[: -len(suffix)]
            break

    base, separator, params = text.partition("(")
    base = base.strip()
    suffix = f"({params}" if separator else ""

    if base in ("timestamp", "time"):
        canonical = f"{base}{qualifier or ' without time zone'}"
    else:
        canonical = _TYPE_NAMES.get(base, base)

    return f"{canonical}{suffix}"


def build_document(source: str, tables: list[RawTable]) -> dict[str, Any]:
    """Turn raw facts into the document, the same way for every source.

    `table_count` counts every table we found, even the ones dropped because
    the project has more tables than we are willing to describe. `truncated`
    says whether that happened, so a consumer can tell "this database has three
    tables" from "this database has three hundred and we showed you the first
    few".
    """
    ordered = sorted(tables, key=lambda table: table.name)
    total = len(ordered)
    visible = ordered[:MAX_TABLES]

    return {
        "source": source,
        "table_count": total,
        "truncated": total > MAX_TABLES,
        "tables": [_table_document(table) for table in visible],
    }


def _table_document(table: RawTable) -> dict[str, Any]:
    primary_key = list(table.primary_key)

    return {
        "name": table.name,
        "columns": [
            {
                "name": column.name,
                "type": canonical_type(column.type),
                "nullable": column.nullable,
                "primary_key": column.name in primary_key,
            }
            for column in table.columns
        ],
        "primary_key": primary_key,
        "unique": [
            list(columns) for columns in sorted({tuple(c) for c in table.unique})
        ],
        "foreign_keys": sorted(
            (
                {
                    "columns": list(foreign_key.columns),
                    "references": foreign_key.references,
                    **(
                        {"on_delete": foreign_key.on_delete}
                        if foreign_key.on_delete
                        else {}
                    ),
                }
                for foreign_key in table.foreign_keys
            ),
            key=lambda foreign_key: foreign_key["columns"],
        ),
    }
