"""A question asked in English, answered in SQL.

The `app.llm` package knows how to talk to a provider and how to read the
reply it gets back. It does not know what a question is for, who asked it, or
that the answer is worth keeping. That is what this module is for.

The LLM service arrives as a parameter rather than being imported here. That
is what makes the whole path testable: a fake provider, no network, no key.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    PlanNotAllowed,
    QueryNotFound,
    WriteNotConfirmed,
    WritesDisabled,
)
from app.llm.service import LLMService
from app.models.project import Project
from app.models.query import Query
from app.services import sql_guard
from app.services.query_executor import QueryExecutor
from app.services.quota import QuotaService


class QueryService:
    """Everything that answers a question, or remembers that it was answered.

    The session arrives in the constructor and every method uses it. Methods do
    not commit; the caller owns the unit of work.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.quota = QuotaService(session)

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
        allowance = await self.quota.allowance(project.owner_id)
        self.quota.assert_can_ask(allowance)
        plan = allowance.plan

        # The project says the customer is willing to write to its own
        # database; the plan says what we sell. Both have to say yes, so
        # neither can answer for the other.
        writes_allowed = project.writes_enabled and plan.allows_writes

        completion = await llm.generate(
            question=question,
            schema_doc=schema_doc,
            writes_enabled=writes_allowed,
        )
        answer = llm.parse_result(
            completion.text, finish_reason=completion.finish_reason
        )
        sql_guard.assert_safe(
            sql_guard.inspect(answer["sql"]),
            writes_enabled=writes_allowed,
        )

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

    async def execute(
        self,
        project: Project,
        query_id: uuid.UUID,
        *,
        limit: int,
        confirmed: bool,
        executor: QueryExecutor,
    ) -> dict:
        """Run a query this project generated earlier, and return the rows.

        The SQL is checked again here, not only when it was generated. The
        check costs nothing, and the statement that runs is not necessarily
        the statement that was written -- anything able to change a row
        between the two is a hole in the guarantee.

        A write needs two things: a project that is allowed to write, and a
        caller that says it means it. The second is not ceremony. A read can
        be repeated and a write cannot, and the difference between "run the
        query I was shown" and "run whatever you have" is one field.

        A write also needs a plan that includes writes. The customer's own
        setting says they are willing; the plan says what the product sells.
        """
        record = await self.session.scalar(
            select(Query).where(
                Query.id == query_id,
                Query.project_id == project.id,
            )
        )
        if record is None:
            raise QueryNotFound("No such query for this project.")

        plan = await self.quota.plan_for(project.owner_id)
        writes_allowed = project.writes_enabled and plan.allows_writes

        statement = sql_guard.inspect(record.sql)

        if statement.is_write:
            if not plan.allows_writes:
                raise PlanNotAllowed(
                    f"Your {plan.label} plan does not include write access. "
                    "Upgrade the plan to run queries that change data."
                )
            if not project.writes_enabled:
                raise WritesDisabled(
                    "This project does not allow queries that change data."
                )
            if not confirmed:
                raise WriteNotConfirmed(
                    "This query changes data. Send it again with confirm: true."
                )

        self.quota.assert_rows_allowed(plan, limit)

        sql_guard.assert_safe(statement, writes_enabled=writes_allowed)

        result = await executor.execute(
            project,
            statement,
            limit=limit,
            max_rows=plan.max_rows,
            timeout_ms=plan.execution_timeout_ms,
        )

        record.executed_at = result.executed_at
        record.row_count = result.row_count
        record.execution_ms = result.execution_ms
        await self.session.flush()

        return {
            "query_id": record.id,
            "sql": record.sql,
            "columns": result.columns,
            "rows": result.rows,
            "row_count": result.row_count,
            "truncated": result.truncated,
            "is_write": result.is_write,
            "execution_ms": result.execution_ms,
            "executed_at": result.executed_at,
        }
