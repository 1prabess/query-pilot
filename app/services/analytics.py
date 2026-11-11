"""An answer that comes with charts.

Each chart is one statement, run on its own: a chart whose statement fails
carries the error, and the charts around it still come back. The model says
which column it means; the rows say what that column holds.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, LLMError
from app.llm.base import TRUNCATED_REASONS
from app.llm.parse import parse_json_response, salvage_visualizations
from app.llm.service import LLMService
from app.models.project import Project
from app.models.query import Query
from app.models.team_membership import TeamMembership
from app.schemas.analytics import (
    AnalyticsPayload,
    ChartPlan,
    ChartSpec,
    ColumnSpec,
    FieldRef,
    FieldSpec,
    FieldType,
    VisualizationPlan,
)
from app.services import permissions, sql_guard
from app.services.query_executor import QueryExecutor
from app.services.quota import QuotaService

# How many charts one answer may carry. Each one is a statement run against
# the customer's database.
MAX_CHARTS = 6

# How many rows one chart may bring back. A year of daily figures is three
# hundred and sixty-five rows.
CHART_ROW_LIMIT = 1000

# The most slices a pie may have. More than that is a bar: the slices get
# too thin to read. A legend beside the pie carries the names, so the cap is
# generous.
MAX_SLICES = 20

# The PostgreSQL types that hold a number, and the ones that hold a point in
# time. Everything else names something.
QUANTITATIVE_TYPES = frozenset(
    {"int2", "int4", "int8", "float4", "float8", "numeric", "money"}
)
TEMPORAL_TYPES = frozenset(
    {"date", "timestamp", "timestamptz", "time", "timetz", "interval"}
)


class AnalyticsService:
    """Everything that answers a question with charts.

    The session arrives in the constructor and every method uses it. Methods do
    not commit; the caller owns the unit of work.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.quota = QuotaService(session)

    async def answer(
        self,
        project: Project,
        membership: TeamMembership,
        question: str,
        *,
        schema_doc: dict,
        llm: LLMService,
        executor: QueryExecutor,
    ) -> dict:
        """Answer a question about this project's database, with charts."""
        permissions.require(membership.role, permissions.ASK)

        allowance = await self.quota.allowance_for_team(project.team_id)
        self.quota.assert_can_ask(allowance)
        plan = allowance.plan

        completion = await llm.generate_analytics(
            question=question, schema_doc=schema_doc, max_charts=MAX_CHARTS
        )
        payload = _payload(completion.text, completion.finish_reason)

        visualizations = []
        for index, planned in enumerate(payload.visualizations[:MAX_CHARTS]):
            visualizations.append(
                await self._one(
                    project,
                    membership,
                    question,
                    planned,
                    plan=plan,
                    executor=executor,
                    completion=completion,
                    # One reply answered every chart, so the cost is written
                    # once.
                    carries_cost=index == 0,
                )
            )

        return {
            "question": question,
            "summary": payload.summary,
            "visualizations": visualizations,
        }

    async def _one(
        self,
        project: Project,
        membership: TeamMembership,
        question: str,
        planned: VisualizationPlan,
        *,
        plan,
        executor: QueryExecutor,
        completion,
        carries_cost: bool,
    ) -> dict:
        """One chart: check the statement, run it, and work out how to draw it.

        A statement that cannot run is this chart's problem, not the
        answer's.
        """
        try:
            statement = sql_guard.inspect(planned.sql)
            sql_guard.assert_safe(statement, writes_enabled=False)
        except AppError as exc:
            return await self._failed(
                project,
                membership,
                question,
                planned,
                exc.message,
                completion=completion,
                carries_cost=carries_cost,
            )

        try:
            result = await executor.execute(
                project,
                statement,
                limit=CHART_ROW_LIMIT,
                max_rows=plan.max_rows,
                timeout_ms=plan.execution_timeout_ms,
            )
        except AppError as exc:
            return await self._failed(
                project,
                membership,
                question,
                planned,
                exc.message,
                completion=completion,
                carries_cost=carries_cost,
            )

        columns = _columns(result.columns, result.column_types)
        chart = _resolve_chart(planned.chart, columns, result.rows)

        record = await self._record(
            project,
            membership,
            question,
            sql=planned.sql,
            explanation=planned.description,
            status="success",
            model=completion.model,
            completion=completion if carries_cost else None,
        )

        return {
            "title": planned.title,
            "description": planned.description,
            "sql": planned.sql,
            "query_id": record.id,
            "chart": chart.model_dump(),
            "columns": [column.model_dump() for column in columns],
            "rows": result.rows,
            "row_count": result.row_count,
            "truncated": result.truncated,
            "execution_ms": result.execution_ms,
            "error": None,
        }

    async def _failed(
        self,
        project: Project,
        membership: TeamMembership,
        question: str,
        planned: VisualizationPlan,
        message: str,
        *,
        completion,
        carries_cost: bool,
    ) -> dict:
        """A chart that did not work, and what went wrong with it."""
        record = await self._record(
            project,
            membership,
            question,
            sql=planned.sql,
            explanation=planned.description,
            status="failed",
            model=completion.model,
            completion=completion if carries_cost else None,
        )

        return {
            "title": planned.title,
            "description": planned.description,
            "sql": planned.sql,
            "query_id": record.id,
            "chart": None,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "truncated": False,
            "execution_ms": 0,
            "error": message,
        }

    async def _record(
        self,
        project: Project,
        membership: TeamMembership,
        question: str,
        *,
        sql: str,
        explanation: str,
        status: str,
        model: str | None,
        completion=None,
    ) -> Query:
        """Keep the statement, so the chart can be run again on its own.

        One row per chart. The model's cost is written on the first row
        only, since one reply answered every chart.
        """
        record = Query(
            project_id=project.id,
            author_id=membership.user_id,
            question=question,
            sql=sql,
            explanation=explanation,
            llm_model=model,
            prompt_tokens=completion.prompt_tokens if completion else None,
            completion_tokens=completion.completion_tokens if completion else None,
            status=status,
        )
        self.session.add(record)
        await self.session.flush()
        return record


def _payload(text: str, finish_reason: str | None) -> AnalyticsPayload:
    """The reply, as a payload.

    A reply cut off mid-way may hold some of its charts. Those are kept.
    """
    parsed = parse_json_response(text)
    if parsed is not None:
        try:
            return AnalyticsPayload.model_validate(parsed)
        except ValidationError:
            pass

    if finish_reason not in TRUNCATED_REASONS:
        raise LLMError(
            "The AI returned a reply that was not in the expected format. "
            "Try asking the question again."
        )

    salvaged = _salvaged(text)
    if not salvaged:
        raise LLMError(
            "The AI ran out of room before it finished answering. "
            "Try a narrower question, or ask for one thing at a time."
        )
    return AnalyticsPayload(summary="", visualizations=salvaged)


def _salvaged(text: str) -> list[VisualizationPlan]:
    """The charts that survived a reply that was cut off."""
    plans: list[VisualizationPlan] = []
    for candidate in salvage_visualizations(text):
        try:
            plans.append(VisualizationPlan.model_validate(candidate))
        except ValidationError:
            continue
    return plans


def _columns(names: list[str], types: list[str]) -> list[ColumnSpec]:
    """The result's columns, and what each one holds."""
    return [
        ColumnSpec(
            name=name,
            type=types[index] if index < len(types) else "",
            kind=_kind_of(types[index] if index < len(types) else ""),
        )
        for index, name in enumerate(names)
    ]


def _kind_of(pg_type: str) -> FieldType:
    """What a PostgreSQL type means to a chart.

    An array is named after its element with an underscore in front, so
    `_int4` is an array of integers. There is no way to draw one on an
    axis, so it names something instead.
    """
    name = pg_type.lower()
    if name.startswith("_"):
        return "categorical"
    if name in QUANTITATIVE_TYPES:
        return "quantitative"
    if name in TEMPORAL_TYPES:
        return "temporal"
    return "categorical"


def _resolve_chart(
    plan: ChartPlan | None,
    columns: list[ColumnSpec],
    rows: list[list[Any]],
) -> ChartSpec:
    """The chart, as it will be drawn.

    The model said what it wanted; the rows say what there is. Where the two
    disagree the rows win.
    """
    by_name = {column.name: column for column in columns}

    # One number is a tile, not a chart.
    if len(rows) == 1 and len(columns) == 1 and columns[0].kind == "quantitative":
        return ChartSpec(
            kind="number",
            y=[FieldSpec(field=columns[0].name, type="quantitative")],
        )

    if plan is None:
        return ChartSpec(kind="table")

    kind = plan.kind
    x = _field(plan.x, by_name)
    y = [
        field
        for field in (_field(reference, by_name) for reference in plan.y)
        if field is not None and field.type == "quantitative"
    ]
    series = _field(plan.series, by_name)

    if kind in ("line", "area", "bar", "stacked_bar", "scatter"):
        # A scatter needs two numbers. A date along one axis is a line.
        needs_two_numbers = kind == "scatter" and (
            x is None or x.type not in ("quantitative", "temporal")
        )
        if x is None or not y or needs_two_numbers:
            kind = "table"
        elif kind == "stacked_bar" and series is None:
            # Nothing to stack without a second dimension to stack by.
            kind = "bar"
    elif kind in ("pie", "donut"):
        y = y[:1]
        series = None
        if x is None or not y:
            kind = "table"
        elif (
            x.type == "temporal"
            or _distinct(rows, columns, x.field) > MAX_SLICES
        ):
            kind = "bar"
    elif kind == "number":
        kind = "table"

    if kind == "table":
        return ChartSpec(kind="table")

    return ChartSpec(kind=kind, x=x, y=y, series=series)


def _field(
    reference: FieldRef | None, by_name: dict[str, ColumnSpec]
) -> FieldSpec | None:
    """The column a reference points at, with its type.

    `None` when the result does not have that column.
    """
    if reference is None:
        return None
    column = by_name.get(reference.field)
    if column is None:
        return None
    return FieldSpec(field=column.name, type=column.kind, format=reference.format)


def _distinct(
    rows: list[list[Any]], columns: list[ColumnSpec], field: str
) -> int:
    """How many different values a column holds."""
    index = next(
        (position for position, column in enumerate(columns) if column.name == field),
        None,
    )
    if index is None:
        return 0
    return len({row[index] for row in rows if index < len(row)})
