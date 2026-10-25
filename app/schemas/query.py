from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.services.query_executor import DEFAULT_ROW_LIMIT, MAX_ROWS


class AskQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class QueryResponse(BaseModel):
    """One answered question.

    The ORM snippets are part of the answer, not part of the record: they are
    the same query written three more ways, for a reader to copy into their
    own code. What gets stored is the SQL and where it came from.
    """

    id: UUID
    question: str
    sql: str
    explanation: str
    assumptions: str
    orm_sqlalchemy: str
    orm_typeorm: str
    orm_prisma: str
    llm_model: str
    prompt_tokens: int
    completion_tokens: int
    status: str
    created_at: datetime


class ExecuteQueryRequest(BaseModel):
    """How many rows the caller wants back, and whether they mean it.

    `confirm` is only read for a query that changes data. Without it, a
    write is refused: the caller has to say it read the SQL and meant to run
    it, which is the difference between running a query and firing one.
    """

    limit: int = Field(default=DEFAULT_ROW_LIMIT, ge=1, le=MAX_ROWS)
    confirm: bool = False


class ExecuteQueryResponse(BaseModel):
    """The rows a query returned.

    The columns are kept apart from the rows: a result set has columns even
    when it has no rows, and a row written as an object would repeat every
    column name on every row of the answer.
    """

    query_id: UUID
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    is_write: bool
    execution_ms: int
    executed_at: datetime
