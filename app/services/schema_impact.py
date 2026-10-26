"""Which saved queries a schema change breaks.

The questions a project has asked are kept with their SQL, so a column that
has gone away can be traced back to the answers that will now fail.

A query that names a column without saying which table it belongs to is
counted against every change to that column name. It cannot be placed more
precisely than that, and a warning that is too wide is better than one that
misses.
"""

from __future__ import annotations

import uuid

import sqlglot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlglot import exp
from sqlglot.errors import SqlglotError

from app.models.query import Query

# A column with no table in front of it.
ANY_TABLE = ""


async def column_references(
    session: AsyncSession, project_id: uuid.UUID
) -> dict[tuple[str, str], list[uuid.UUID]]:
    """Every column each saved query refers to, and the queries that do.

    The SQL is parsed once per query and the answer kept, so a page of
    changes costs one pass over the project's questions rather than one per
    change.
    """
    rows = await session.execute(
        select(Query.id, Query.sql).where(Query.project_id == project_id)
    )

    references: dict[tuple[str, str], list[uuid.UUID]] = {}
    for query_id, sql in rows:
        for table, column in _referenced_columns(sql):
            references.setdefault((table, column), []).append(query_id)
    return references


def affected_queries(
    references: dict[tuple[str, str], list[uuid.UUID]],
    table: str,
    column: str | None,
) -> list[uuid.UUID]:
    """The saved queries that refer to a column a change touched."""
    if column is None:
        return []

    hits: set[uuid.UUID] = set()
    for (referenced_table, referenced_column), query_ids in references.items():
        if referenced_column != column:
            continue
        if referenced_table in (table, ANY_TABLE):
            hits.update(query_ids)
    return sorted(hits)


def _referenced_columns(sql: str) -> set[tuple[str, str]]:
    """Every table and column a statement names."""
    if not sql or not sql.strip():
        return set()

    try:
        parsed = sqlglot.parse_one(sql, dialect="postgres")
    except SqlglotError:
        return set()
    if parsed is None:
        return set()

    aliases = _aliases(parsed)

    found: set[tuple[str, str]] = set()
    for column in parsed.find_all(exp.Column):
        qualifier = column.table or ANY_TABLE
        found.add((aliases.get(qualifier, qualifier), column.name))
    return found


def _aliases(parsed: exp.Expr) -> dict[str, str]:
    """The alias each table was given, and the table behind it.

    `FROM orders o` means `o.total` is `orders.total`, and a change to
    `orders.total` is a change to what that query reads.
    """
    aliases: dict[str, str] = {}
    for table in parsed.find_all(exp.Table):
        name = table.name
        alias = table.alias_or_name
        if alias and alias != name:
            aliases[alias] = name
    return aliases
