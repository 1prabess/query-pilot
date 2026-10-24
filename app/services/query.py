"""A question asked in English, answered in SQL.

The `app.llm` package knows how to talk to a provider and how to read the
reply it gets back. It does not know what a question is for, who asked it, or
that the answer is worth keeping. That is what this module is for.

The LLM service arrives as a parameter rather than being imported here. That
is what makes the whole path testable: a fake provider, no network, no key.
"""

from __future__ import annotations

import sqlglot
from sqlalchemy.ext.asyncio import AsyncSession
from sqlglot import exp

from app.core.errors import UnsafeQuery
from app.llm.service import LLMService
from app.models.project import Project
from app.models.query import Query

# Nodes that change something. The DML classes are spelled out one by one
# because sqlglot keeps `DML` itself out of its public exports. `DDL` covers
# CREATE/DROP/ALTER. The rest are statements that are neither a query nor
# caught by those two: `SELECT ... INTO` creates a table, `FOR UPDATE` locks
# rows, `SET` changes the session, `USE` switches database.
_FORBIDDEN = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Copy,
    exp.DDL,
    exp.Command,
    exp.TruncateTable,
    exp.Into,
    exp.Lock,
    exp.Set,
    exp.Use,
)


def assert_read_only(sql: str) -> None:
    """Refuse anything that is not a single read-only statement.

    The prompt asks for one SELECT, and the model nearly always complies.
    "Nearly" is the problem: the customer database is read-only as far as this
    product is concerned, and that has to hold even on the replies where it
    does not.

    The whole tree is walked, not just the root. `WITH x AS (DELETE FROM t
    RETURNING *) SELECT * FROM x` parses as a SELECT -- the DELETE is inside
    it -- and `SELECT * FROM t FOR UPDATE` is a SELECT that locks rows.
    """
    try:
        parsed = sqlglot.parse(sql, dialect="postgres")
    except sqlglot.ParseError as exc:
        raise UnsafeQuery("The generated query could not be parsed.") from exc

    statements = [statement for statement in parsed if statement is not None]
    if len(statements) != 1:
        raise UnsafeQuery("The generated query was more than one statement.")

    statement = statements[0]
    if not isinstance(statement, exp.Query):
        raise UnsafeQuery("The generated query was not a SELECT.")

    for node in statement.walk():
        if isinstance(node, _FORBIDDEN):
            raise UnsafeQuery("The generated query was not read-only.")


class QueryService:
    """Everything that answers a question, or remembers that it was answered.

    The session arrives in the constructor and every method uses it. Methods do
    not commit; the caller owns the unit of work.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def ask(
        self,
        project: Project,
        question: str,
        *,
        schema_doc: dict,
        llm: LLMService,
    ) -> dict:
        """Answer a question about this project's database.

        The answer is recorded before it is returned. A query the customer ran
        is worth having afterwards, and the answer is worthless if the record
        of it was lost when the request ended.
        """
        completion = await llm.generate(question=question, schema_doc=schema_doc)
        answer = llm.parse_result(
            completion.text, finish_reason=completion.finish_reason
        )
        assert_read_only(answer["sql"])

        record = Query(
            project_id=project.id,
            question=question,
            sql=answer["sql"],
            explanation=answer["explanation"],
            assumptions=answer["assumptions"],
            llm_model=completion.model,
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            status="success",
        )
        self.session.add(record)
        await self.session.flush()

        return {
            "id": record.id,
            "question": record.question,
            "sql": answer["sql"],
            "explanation": answer["explanation"],
            "assumptions": answer["assumptions"],
            "orm_sqlalchemy": answer["orm_sqlalchemy"],
            "orm_typeorm": answer["orm_typeorm"],
            "orm_prisma": answer["orm_prisma"],
            "llm_model": completion.model,
            "prompt_tokens": completion.prompt_tokens,
            "completion_tokens": completion.completion_tokens,
            "status": record.status,
            "created_at": record.created_at,
        }
