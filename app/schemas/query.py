from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


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
