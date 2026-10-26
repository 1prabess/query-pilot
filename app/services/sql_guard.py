"""Decide what a statement is, and whether we are willing to run it.

The customer asked a question in English and the model answered it in SQL.
What arrives here is that SQL, and this is the only place that decides
whether it runs.

Two questions are asked of the same parsed statement: is this a write, and
are we willing to run it at all. The answer to the first decides which of
the two ways it is run -- a read in a read-only transaction, a write in a
transaction that commits.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from app.core.errors import UnsafeQuery

# The statements that change rows.
#
# `Insert` is spelled out alongside the others because sqlglot makes it a
# subclass of `DDL` rather than of `DML`: it changes rows, and a check for
# `DDL` alone would call it a schema change.
_WRITE_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Merge)

# The statements that are never acceptable, in write mode as much as out of
# it. `Copy` moves data between a table and a file. `TruncateTable` empties
# one. `Into` creates a table from a SELECT. `Lock` takes row locks. `Set`
# changes the session. `Use` switches database.
_FORBIDDEN = (
    exp.Copy,
    exp.Command,
    exp.TruncateTable,
    exp.Into,
    exp.Lock,
    exp.Set,
    exp.Use,
)


@dataclass(frozen=True)
class Statement:
    """One statement, parsed, and what we decided about it."""

    sql: str
    parsed: exp.Expr
    is_write: bool

    @property
    def has_returning(self) -> bool:
        """Does the statement hand back the rows it changed?"""
        return self.parsed.find(exp.Returning) is not None


def inspect(sql: str) -> Statement:
    """Parse `sql` and work out what it is.

    A statement that cannot be parsed, or that is more than one statement,
    is refused here rather than later: the customer gets one error either
    way, and this is where the check belongs.
    """
    try:
        parsed = sqlglot.parse(sql, dialect="postgres")
    except sqlglot.ParseError as exc:
        raise UnsafeQuery("The generated query could not be parsed.") from exc

    statements = [statement for statement in parsed if statement is not None]
    if len(statements) != 1:
        raise UnsafeQuery("The generated query was more than one statement.")

    statement = statements[0]
    return Statement(
        sql=sql,
        parsed=statement,
        is_write=any(isinstance(node, _WRITE_NODES) for node in statement.walk()),
    )


def assert_safe(statement: Statement, *, writes_enabled: bool) -> None:
    """Refuse anything we are not willing to run.

    The whole tree is walked, not just the root. `WITH x AS (DELETE FROM t
    RETURNING *) SELECT * FROM x` parses as a SELECT -- the DELETE is inside
    it -- and `SELECT * FROM t FOR UPDATE` is a SELECT that locks rows.
    """
    root = statement.parsed

    if not writes_enabled:
        if not isinstance(root, exp.Query):
            raise UnsafeQuery("The generated query was not a SELECT.")
        for node in root.walk():
            if isinstance(node, _WRITE_NODES + _FORBIDDEN + (exp.DDL,)):
                raise UnsafeQuery("The generated query was not read-only.")
        return

    if not isinstance(root, (exp.Query, *_WRITE_NODES)):
        raise UnsafeQuery("The generated query was not a statement we can run.")
    for node in root.walk():
        if isinstance(node, _WRITE_NODES):
            continue
        if isinstance(node, exp.DDL):
            raise UnsafeQuery("The generated query changes the schema.")
        if isinstance(node, _FORBIDDEN):
            raise UnsafeQuery("The generated query was not allowed to run.")
