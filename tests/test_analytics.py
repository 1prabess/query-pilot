"""Tests for the analytics answer.

The parts worth testing are the decisions: which charts survive a reply that
was cut off, and how a chart is worked out from the rows that came back. The
rest is plumbing that the API exercises end to end.
"""

import uuid

import pytest

from app.core.errors import LLMError
from app.llm.base import Completion
from app.llm.parse import salvage_visualizations
from app.models.project import Project
from app.models.team_membership import TeamMembership
from app.schemas.analytics import ChartPlan, FieldRef, VisualizationPlan
from app.services.analytics import (
    AnalyticsService,
    _columns,
    _kind_of,
    _payload,
    _resolve_chart,
)
from app.services.query_executor import QueryResult


class _FakeSession:
    """A session that writes down what it is given and nothing else."""

    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, instance: object) -> None:
        self.added.append(instance)

    async def flush(self) -> None:
        pass


class _FakeExecutor:
    """An executor that returns what it was built with."""

    def __init__(self, result: QueryResult) -> None:
        self.result = result

    async def execute(self, project, statement, *, limit, max_rows, timeout_ms):
        return self.result


def _result(columns, types, rows) -> QueryResult:
    import datetime as dt

    return QueryResult(
        columns=columns,
        column_types=types,
        rows=rows,
        row_count=len(rows),
        truncated=False,
        is_write=False,
        execution_ms=1,
        executed_at=dt.datetime.now(dt.UTC),
    )


def _chart_plan(kind="line", x=None, y=None, series=None) -> ChartPlan:
    return ChartPlan(
        kind=kind,
        x=FieldRef(field=x) if x else None,
        y=[FieldRef(field=field) for field in (y or [])],
        series=FieldRef(field=series) if series else None,
    )


class TestSalvage:
    def test_a_cut_off_reply_keeps_the_charts_that_arrived(self):
        text = (
            '{"summary": "some words", "visualizations": ['
            '{"title": "Revenue", "sql": "SELECT 1 AS n", '
            '"chart": {"kind": "table"}}, '
            '{"title": "Orders", "sql": "SELECT 2 AS n", "chart": {"kind": '
        )

        salvaged = salvage_visualizations(text)

        assert [chart["title"] for chart in salvaged] == ["Revenue"]

    def test_a_reply_that_is_whole_is_salvaged_whole(self):
        text = '{"summary": "s", "visualizations": [{"title": "A", "sql": "SELECT 1"}]}'

        salvaged = salvage_visualizations(text)

        assert len(salvaged) == 1
        assert salvaged[0]["title"] == "A"

    def test_a_reply_with_nothing_in_it_salvages_nothing(self):
        assert salvage_visualizations("I could not answer that.") == []


class TestPayload:
    def test_a_whole_reply_is_a_payload(self):
        text = (
            '{"summary": "Revenue is up.", "visualizations": ['
            '{"title": "Revenue", "sql": "SELECT 1 AS revenue", '
            '"chart": {"kind": "bar", "x": {"field": "month"}, '
            '"y": [{"field": "revenue"}]}}]}'
        )

        payload = _payload(text, None)

        assert payload.summary == "Revenue is up."
        assert payload.visualizations[0].title == "Revenue"

    def test_a_reply_that_is_not_the_expected_shape_is_refused(self):
        with pytest.raises(LLMError):
            _payload("this is not json", None)

    def test_a_cut_off_reply_keeps_the_charts_that_arrived(self):
        text = (
            '{"summary": "s", "visualizations": ['
            '{"title": "Revenue", "sql": "SELECT 1 AS revenue"}, '
            '{"title": "Orders", "sql": "SELECT 2 AS ord'
        )

        payload = _payload(text, "MAX_TOKENS")

        assert [chart.title for chart in payload.visualizations] == ["Revenue"]

    def test_a_cut_off_reply_with_nothing_in_it_is_refused(self):
        with pytest.raises(LLMError):
            _payload('{"summary": "s", "visualizations": [{"tit', "MAX_TOKENS")


class TestColumnKinds:
    @pytest.mark.parametrize(
        "pg_type,kind",
        [
            ("int4", "quantitative"),
            ("int8", "quantitative"),
            ("numeric", "quantitative"),
            ("float8", "quantitative"),
            ("money", "quantitative"),
            ("timestamptz", "temporal"),
            ("date", "temporal"),
            ("interval", "temporal"),
            ("text", "categorical"),
            ("varchar", "categorical"),
            ("bool", "categorical"),
            ("uuid", "categorical"),
            ("jsonb", "categorical"),
            ("bytea", "categorical"),
            ("_int4", "categorical"),
        ],
    )
    def test_a_type_is_what_it_means_to_a_chart(self, pg_type, kind):
        assert _kind_of(pg_type) == kind


class TestResolveChart:
    def test_one_number_is_a_tile_and_not_a_chart(self):
        columns = _columns(["total"], ["numeric"])

        chart = _resolve_chart(_chart_plan("line", x="total", y=["total"]), columns, [[1]])

        assert chart.kind == "number"
        assert chart.y[0].field == "total"

    def test_a_field_that_is_not_in_the_result_falls_back_to_a_table(self):
        columns = _columns(["month", "revenue"], ["timestamptz", "numeric"])

        chart = _resolve_chart(
            _chart_plan("line", x="month", y=["turnover"]), columns, [["2026-01-01", 1]]
        )

        assert chart.kind == "table"

    def test_a_field_that_is_not_a_number_is_not_plotted(self):
        columns = _columns(["month", "label"], ["timestamptz", "text"])

        chart = _resolve_chart(
            _chart_plan("line", x="month", y=["label"]), columns, [["2026-01-01", "a"]]
        )

        assert chart.kind == "table"

    def test_a_pie_with_too_many_slices_becomes_a_bar(self):
        columns = _columns(["plan", "revenue"], ["text", "numeric"])
        rows = [[f"plan {index}", index] for index in range(20)]

        chart = _resolve_chart(
            _chart_plan("pie", x="plan", y=["revenue"]), columns, rows
        )

        assert chart.kind == "bar"

    def test_a_pie_over_time_becomes_a_bar(self):
        columns = _columns(["month", "revenue"], ["timestamptz", "numeric"])

        chart = _resolve_chart(
            _chart_plan("pie", x="month", y=["revenue"]), columns, [["2026-01-01", 1]]
        )

        assert chart.kind == "bar"

    def test_a_pie_that_fits_stays_a_pie(self):
        columns = _columns(["plan", "revenue"], ["text", "numeric"])
        rows = [["basic", 1], ["pro", 2]]

        chart = _resolve_chart(
            _chart_plan("pie", x="plan", y=["revenue"]), columns, rows
        )

        assert chart.kind == "pie"
        assert chart.x.field == "plan"

    def test_a_scatter_needs_two_numbers(self):
        columns = _columns(["plan", "revenue"], ["text", "numeric"])

        chart = _resolve_chart(
            _chart_plan("scatter", x="plan", y=["revenue"]), columns, [["basic", 1]]
        )

        assert chart.kind == "table"

    def test_a_line_over_a_number_is_still_a_line(self):
        columns = _columns(["score", "revenue"], ["int4", "numeric"])

        chart = _resolve_chart(
            _chart_plan("line", x="score", y=["revenue"]), columns, [[1, 2]]
        )

        assert chart.kind == "line"
        assert chart.x.type == "quantitative"

    def test_a_stacked_bar_with_nothing_to_stack_is_a_bar(self):
        columns = _columns(["month", "revenue"], ["timestamptz", "numeric"])

        chart = _resolve_chart(
            _chart_plan("stacked_bar", x="month", y=["revenue"]), columns, [[1, 2]]
        )

        assert chart.kind == "bar"

    def test_a_stacked_bar_with_a_series_stays_a_stacked_bar(self):
        columns = _columns(["month", "plan", "revenue"], ["timestamptz", "text", "numeric"])

        chart = _resolve_chart(
            _chart_plan("stacked_bar", x="month", y=["revenue"], series="plan"),
            columns,
            [["2026-01-01", "pro", 2]],
        )

        assert chart.kind == "stacked_bar"
        assert chart.series.field == "plan"


class TestFieldRef:
    def test_a_field_named_as_a_bare_string_is_read(self):
        assert FieldRef.model_validate("status").field == "status"

    def test_a_field_named_as_an_object_is_read(self):
        assert FieldRef.model_validate({"field": "status"}).field == "status"

    def test_a_chart_with_a_bare_series_is_read(self):
        plan = ChartPlan.model_validate(
            {
                "kind": "bar",
                "x": "month",
                "y": ["revenue"],
                "series": "status",
            }
        )

        assert plan.x.field == "month"
        assert plan.y[0].field == "revenue"
        assert plan.series.field == "status"


class TestOneChart:
    def _service(self):
        return AnalyticsService(_FakeSession())

    def _project_and_membership(self):
        team_id = uuid.uuid4()
        return (
            Project(id=uuid.uuid4(), team_id=team_id, name="Shop"),
            TeamMembership(team_id=team_id, user_id=uuid.uuid4(), role="owner"),
        )

    def _plan(self):
        return VisualizationPlan(
            title="Revenue",
            description="Revenue by month",
            sql="SELECT 1 AS revenue",
            chart=_chart_plan("bar", x="month", y=["revenue"]),
        )

    async def test_a_chart_that_runs_comes_back_with_its_data(self):
        project, membership = self._project_and_membership()
        service = self._service()
        executor = _FakeExecutor(
            _result(
                ["month", "revenue"],
                ["timestamptz", "numeric"],
                [["2026-01-01T00:00:00+00:00", "10.00"]],
            )
        )

        chart = await service._one(
            project,
            membership,
            "how much revenue",
            self._plan(),
            plan=_plan(),
            executor=executor,
            completion=Completion(text="", model="test"),
            carries_cost=True,
        )

        assert chart["error"] is None
        assert chart["chart"]["kind"] == "bar"
        assert chart["columns"][1] == {
            "name": "revenue",
            "type": "numeric",
            "kind": "quantitative",
        }

    async def test_a_chart_that_writes_is_refused_and_the_answer_survives(self):
        project, membership = self._project_and_membership()
        service = self._service()
        plan = VisualizationPlan(
            title="Delete",
            description="Deletes a row",
            sql="DELETE FROM customers",
        )

        chart = await service._one(
            project,
            membership,
            "delete the customers",
            plan,
            plan=_plan(),
            executor=_FakeExecutor(_result([], [], [])),
            completion=Completion(text="", model="test"),
            carries_cost=True,
        )

        assert chart["error"] is not None
        assert chart["rows"] == []
        assert chart["chart"] is None


def _plan():
    from app.services.plans import FREE_PLAN

    return FREE_PLAN
