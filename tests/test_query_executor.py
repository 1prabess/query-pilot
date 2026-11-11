"""Tests for running a statement against the customer's database.

A customer's role may have no search path -- Neon hands out one whose
search_path is empty -- and the statements we run name their tables without
a schema in front.
"""

import uuid
from typing import Self

from app.models.project import Project
from app.models.project_connection import ProjectConnection
from app.services import sql_guard
from app.services.query_executor import QueryExecutor, _quote_identifier


class _FakePrepared:
    """The statement the connection was asked to prepare."""

    def __init__(self, connection: "_FakeConnection", sql: str) -> None:
        self._connection = connection
        self.sql = sql

    def get_attributes(self) -> list:
        return []

    async def fetch(self, timeout=None) -> list:
        self._connection.executed.append(self.sql)
        return []


class _FakeConnection:
    """A connection that writes down what it was told to do."""

    def __init__(self) -> None:
        self.executed: list[str] = []
        self.prepared: list[str] = []
        self.closed = False

    def transaction(self) -> "_FakeConnection":
        return self

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args) -> bool:
        return False

    async def execute(self, sql: str, timeout=None) -> str:
        self.executed.append(sql)
        return "SELECT 0"

    async def prepare(self, sql: str) -> _FakePrepared:
        self.prepared.append(sql)
        return _FakePrepared(self, sql)

    async def close(self) -> None:
        self.closed = True


def _connect_to(connection: _FakeConnection):
    async def connect(*args, **kwargs) -> _FakeConnection:
        return connection

    return connect


def _project(schema_name: str = "public") -> Project:
    return Project(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        name="Shop",
        connection=ProjectConnection(
            project_id=uuid.uuid4(),
            dsn_encrypted="postgresql://user:pw@host:5432/db",
            schema_name=schema_name,
        ),
    )


class TestSearchPath:
    async def test_a_read_puts_the_schema_on_the_search_path_first(self, monkeypatch):
        connection = _FakeConnection()
        monkeypatch.setattr(
            "app.services.query_executor.asyncpg.connect", _connect_to(connection)
        )

        await QueryExecutor().execute(
            _project(), sql_guard.inspect("SELECT count(*) FROM actor"), limit=10
        )

        assert connection.executed[0] == 'SET LOCAL search_path TO "public"'
        assert "SELECT count(*) FROM actor" in connection.executed[-1]

    async def test_a_write_puts_the_schema_on_the_search_path_first(self, monkeypatch):
        connection = _FakeConnection()
        monkeypatch.setattr(
            "app.services.query_executor.asyncpg.connect", _connect_to(connection)
        )

        await QueryExecutor().execute(
            _project(), sql_guard.inspect("DELETE FROM actor"), limit=10
        )

        assert connection.executed[0] == 'SET LOCAL search_path TO "public"'

    async def test_the_schema_the_project_names_is_the_one_used(self, monkeypatch):
        connection = _FakeConnection()
        monkeypatch.setattr(
            "app.services.query_executor.asyncpg.connect", _connect_to(connection)
        )

        await QueryExecutor().execute(
            _project("billing"),
            sql_guard.inspect("SELECT count(*) FROM invoice"),
            limit=10,
        )

        assert connection.executed[0] == 'SET LOCAL search_path TO "billing"'

    def test_a_schema_name_is_quoted_rather_than_pasted_in(self):
        assert _quote_identifier("public") == '"public"'
        assert _quote_identifier('pub"lic') == '"pub""lic"'
