"""Run a query against the customer's database and bring the rows back.

The customer's database is not ours, and a read is a different thing from a
write. A read runs in a transaction that cannot write, with a cap on the
rows it may bring back. A write runs in a transaction that commits, and it
only gets here once the project has said it may write and the caller has
said it means it.

Either way the connection is to the customer's database, and it is opened
and closed per query -- a query that is never run leaves no connection
behind.
"""

from __future__ import annotations

import base64
import datetime as dt
import decimal
import time
import uuid
from dataclasses import dataclass
from typing import Any

import asyncpg

from app.core.config import Settings, get_settings
from app.core.errors import (
    CustomerDatabaseUnreachable,
    QueryExecutionFailed,
    QueryTimeout,
)
from app.models.project import Project
from app.services.sql_guard import Statement

# The most rows one answer may ever carry, whichever plan is asking. A
# customer who asks for every row of a table with ten million of them gets
# the first few thousand and a note that there are more; the alternative is
# an answer that never arrives. Each plan asks for fewer.
MAX_ROWS = 5000

# The number of rows returned when the caller does not ask for a particular
# number.
DEFAULT_ROW_LIMIT = 100

# How long to wait for the customer's database to accept a connection. A
# database that is not answering is a database we should hear about, not one
# to wait on.
CONNECT_TIMEOUT_SECONDS: int = 10


@dataclass(frozen=True)
class QueryResult:
    """What a query returned, in the shape the API hands it over."""

    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    is_write: bool
    execution_ms: int
    executed_at: dt.datetime


class QueryExecutor:
    """Runs one statement against the customer's database.

    It holds no session of ours. The connection it opens is to the
    customer's database, and it is closed before the method returns.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def execute(
        self,
        project: Project,
        statement: Statement,
        *,
        limit: int,
        max_rows: int | None = None,
        timeout_ms: int | None = None,
    ) -> QueryResult:
        """Run `statement` and return what it produced.

        `max_rows` and `timeout_ms` are the plan's numbers. They are
        ceilings, not requests: the deployment's own limits still apply on
        top, so a plan cannot buy its way past the operator's settings.
        """
        row_cap = MAX_ROWS if max_rows is None else min(MAX_ROWS, max_rows)
        limit = max(1, min(limit, row_cap))
        dsn = project.decrypted_dsn()
        started = time.perf_counter()

        try:
            connection = await asyncpg.connect(
                dsn,
                timeout=CONNECT_TIMEOUT_SECONDS,
                command_timeout=self._timeout_seconds(timeout_ms),
            )
        except Exception as exc:
            raise CustomerDatabaseUnreachable(
                "We could not reach the customer's database."
            ) from exc

        try:
            columns, rows, row_count, truncated = await self._run(
                connection, statement, limit, timeout_ms
            )
        finally:
            await connection.close()

        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=row_count,
            truncated=truncated,
            is_write=statement.is_write,
            execution_ms=int((time.perf_counter() - started) * 1000),
            executed_at=dt.datetime.now(dt.UTC),
        )

    async def _run(
        self,
        connection: asyncpg.Connection,
        statement: Statement,
        limit: int,
        timeout_ms: int | None,
    ) -> tuple[list[str], list[list[Any]], int, bool]:
        """Run the statement, in whichever transaction it needs."""
        try:
            if statement.is_write:
                return await self._write(connection, statement, timeout_ms)
            return await self._read(connection, statement, limit, timeout_ms)
        except (TimeoutError, asyncpg.QueryCanceledError) as exc:
            raise QueryTimeout("The query took too long and was stopped.") from exc
        except asyncpg.PostgresError as exc:
            raise QueryExecutionFailed(f"The query failed: {exc}") from exc
        except (asyncpg.InterfaceError, asyncpg.ConnectionDoesNotExistError) as exc:
            raise CustomerDatabaseUnreachable(
                "We lost the connection to the customer's database."
            ) from exc

    async def _read(
        self,
        connection: asyncpg.Connection,
        statement: Statement,
        limit: int,
        timeout_ms: int | None,
    ) -> tuple[list[str], list[list[Any]], int, bool]:
        """A read, in a transaction that cannot write.

        The read-only transaction is the second wall, not the only one. The
        statement has already been parsed and checked; a transaction that
        cannot write anyway means a statement that slipped past the check
        has nowhere to go.
        """
        sql = _with_row_limit(statement.sql, limit + 1)

        async with connection.transaction():
            await connection.execute("SET TRANSACTION READ ONLY")
            prepared = await connection.prepare(sql)
            columns = [attribute.name for attribute in prepared.get_attributes()]
            records = await prepared.fetch(timeout=self._timeout_seconds(timeout_ms))

        truncated = len(records) > limit
        if truncated:
            records = records[:limit]

        return (
            columns,
            [[_json_value(value) for value in record.values()] for record in records],
            len(records),
            truncated,
        )

    async def _write(
        self,
        connection: asyncpg.Connection,
        statement: Statement,
        timeout_ms: int | None,
    ) -> tuple[list[str], list[list[Any]], int, bool]:
        """A write, in a transaction that commits.

        A write is not capped the way a read is. `LIMIT` around an INSERT
        would change what it means, and a cap that silently drops rows is
        worse than one that does not exist.
        """
        async with connection.transaction():
            if not statement.has_returning:
                status = await connection.execute(
                    statement.sql, timeout=self._timeout_seconds(timeout_ms)
                )
                return ([], [], _rows_affected(status), False)

            prepared = await connection.prepare(statement.sql)
            columns = [attribute.name for attribute in prepared.get_attributes()]
            records = await prepared.fetch(timeout=self._timeout_seconds(timeout_ms))

        return (
            columns,
            [[_json_value(value) for value in record.values()] for record in records],
            len(records),
            False,
        )

    def _timeout_seconds(self, timeout_ms: int | None = None) -> float:
        """The timeout for one statement, in seconds.

        The deployment's setting is the ceiling. A plan's number can hold a
        tier down but never lift it past what the operator allows.
        """
        ceiling_ms = self.settings.QUERY_EXECUTION_TIMEOUT_MS
        if timeout_ms is not None:
            ceiling_ms = min(ceiling_ms, timeout_ms)
        return ceiling_ms / 1000


def _with_row_limit(sql: str, limit: int) -> str:
    """The same statement, with a hard cap on the rows it may return.

    The statement is wrapped rather than edited, so what runs is the query
    that was generated and reviewed, with the database deciding how many rows
    of it come back.
    """
    inner = sql.strip()
    while inner.endswith(";"):
        inner = inner[:-1].rstrip()

    # The newline is not decoration. A statement ending in a `--` comment
    # would otherwise comment out the closing bracket.
    return f"SELECT * FROM ({inner}\n) AS _querypilot_result LIMIT {limit}"


def _rows_affected(status: str) -> int:
    """How many rows a statement changed, from Postgres' status line.

    Postgres reports `INSERT 0 3`, `UPDATE 2`, `DELETE 5`. The count is the
    last word.
    """
    parts = status.split()
    if not parts:
        return 0
    try:
        return int(parts[-1])
    except ValueError:
        return 0


def _json_value(value: Any) -> Any:
    """One database value as something JSON can carry.

    `Decimal` becomes a string rather than a float. JSON has no decimal, and
    the float that stands in for one is a rounding of the value the customer
    stored -- which is the wrong thing to hand back to the person whose money
    it is.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return base64.b64encode(bytes(value)).decode()
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)


_executor: QueryExecutor | None = None


def get_query_executor() -> QueryExecutor:
    """One instance for the process.

    The executor holds no connection and no session -- it opens a connection
    to the customer's database per query and closes it again -- so there is
    nothing here that a second instance would share badly.
    """
    global _executor
    if _executor is None:
        _executor = QueryExecutor()
    return _executor
