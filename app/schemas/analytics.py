"""The analytics answer: a summary, and the charts that back it up.

The model proposes a chart and this module says what a chart *is*. The plan
comes in with field names; the spec goes out with names and types, because
only the backend knows what a column holds.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# The charts a client knows how to draw. Every kind here is one the client
# renders; a kind the client does not know is a chart nobody sees.
ChartKind = Literal[
    "line",
    "area",
    "bar",
    "stacked_bar",
    "pie",
    "donut",
    "scatter",
    "table",
    "number",
]

# What a column holds, as far as drawing is concerned. A column is one of
# three things: something that counts, something that happens in time, and
# something that names.
FieldType = Literal["temporal", "quantitative", "categorical"]

# How a number should be written. The client uses this to format the axis
# and the tooltip; it does not change the value.
NumberFormat = Literal["number", "currency", "percent"]


class FieldRef(BaseModel):
    """A column, named. The model's version: no type."""

    field: str
    format: NumberFormat | None = None

    @model_validator(mode="before")
    @classmethod
    def _bare_name(cls, value: Any) -> Any:
        """Take a field named as a bare string."""
        if isinstance(value, str):
            return {"field": value}
        return value


class ChartPlan(BaseModel):
    """The chart the model asked for."""

    kind: ChartKind
    x: FieldRef | None = None
    y: list[FieldRef] = Field(default_factory=list)
    series: FieldRef | None = None


class VisualizationPlan(BaseModel):
    """One chart the model asked for, and the statement that fills it."""

    title: str
    description: str = ""
    sql: str
    chart: ChartPlan | None = None


class AnalyticsPayload(BaseModel):
    """The whole reply: what the numbers say, and the charts that show it.

    An empty `visualizations` is a real answer: the model may have found
    that the schema does not hold what the question needs, and said so in
    the summary.
    """

    summary: str = ""
    visualizations: list[VisualizationPlan] = Field(default_factory=list)


class FieldSpec(BaseModel):
    """A column, named and typed."""

    field: str
    type: FieldType
    format: NumberFormat | None = None


class ChartSpec(BaseModel):
    """The chart the client is handed: the plan, with the types filled in.

    `x` is the column the chart runs along, `y` the numbers it plots, and
    `series` the column that splits it into several lines or bars.
    """

    kind: ChartKind
    x: FieldSpec | None = None
    y: list[FieldSpec] = Field(default_factory=list)
    series: FieldSpec | None = None


class ColumnSpec(BaseModel):
    """One column of a result, and what it holds."""

    name: str
    type: str
    kind: FieldType


class VisualizationResponse(BaseModel):
    """One chart, with its data.

    `error` is what makes this partial rather than failed: a chart whose
    statement the database refused still comes back, carrying what went
    wrong.
    """

    title: str
    description: str = ""
    sql: str
    query_id: uuid.UUID | None = None
    chart: ChartSpec | None = None
    columns: list[ColumnSpec] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    execution_ms: int = 0
    error: str | None = None


class AnalyticsRequest(BaseModel):
    """The question, in English."""

    question: str = Field(min_length=1, max_length=2000)


class AnalyticsResponse(BaseModel):
    """The answer: a summary, and the charts that back it up."""

    question: str
    summary: str
    visualizations: list[VisualizationResponse] = Field(default_factory=list)
