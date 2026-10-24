"""Read a `pg_dump` file as a schema.

The dump is the awkward input. A live database can be asked what it contains;
a file can only be read, and what it says is spread across `CREATE TABLE`
statements for the columns and `ALTER TABLE ... ADD CONSTRAINT` statements for
the keys, in an order that is not guaranteed to be the order the tables were
created in.

Everything here is pure. It takes text and gives back facts about tables, and
it never touches a database, which is what makes it possible to check the two
readers against each other.
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from app.services.schema_document import RawColumn, RawForeignKey, RawTable

# psql commands and comments. `pg_dump` writes both, and neither is SQL, so
# sqlglot has to be kept away from them. The patterns are line-anchored on
# purpose: a backslash inside a string literal is not a command.
_PSQL_COMMAND = re.compile(r"^[^\S\n]*\\[^\n]*$", re.MULTILINE)
_COMMENT_LINE = re.compile(r"^[^\S\n]*--[^\n]*$", re.MULTILINE)

# The types sqlglot knows by name, spelled the way PostgreSQL prints them.
_TYPE_NAMES = {
    "bigint": "bigint",
    "binary": "bytea",
    "boolean": "boolean",
    "char": "character",
    "date": "date",
    "decimal": "numeric",
    "double": "double precision",
    "float": "real",
    "int": "integer",
    "inet": "inet",
    "json": "json",
    "jsonb": "jsonb",
    "money": "money",
    "smallint": "smallint",
    "text": "text",
    "time": "time without time zone",
    "timestamp": "timestamp without time zone",
    "timestamptz": "timestamp with time zone",
    "timetz": "time with time zone",
    "uuid": "uuid",
    "varchar": "character varying",
    "varbinary": "bytea",
}


class SchemaParseError(Exception):
    """The upload was not a dump we can read.

    Not an `AppError`. The parser knows what went wrong in its own words; the
    service turns that into the error the API reports.
    """


def parse_pg_dump(text: str, *, schema_name: str | None = None) -> list[RawTable]:
    """Every table the dump describes.

    `schema_name` drops tables belonging to another schema. A dump that
    mentions `public.customers` while the project is pointed at `sales` is
    about a different database, and quietly mixing the two would be worse than
    saying so.
    """
    statements = _parse_statements(_strip_noise(text))

    tables: dict[str, RawTable] = {}
    pending: dict[str, list[exp.Expr]] = {}
    created = 0

    for statement in statements:
        if isinstance(statement, exp.Create):
            if statement.args.get("kind") == "TABLE":
                created += 1
                table = _table_from_create(statement, schema_name)
                if table is None:
                    continue
                tables[table.name] = table
                held = pending.pop(table.name, [])
                if held:
                    _apply_constraints(table, held)
            else:
                _apply_unique_index(statement, tables, schema_name)
        elif isinstance(statement, exp.Alter):
            _apply_alter(statement, tables, pending, schema_name)

    if not created:
        raise SchemaParseError("No CREATE TABLE statements found in the upload.")

    # A dump full of tables, none of them in the schema this project is about,
    # is not a broken upload. It is an empty answer, and the caller says so.
    return list(tables.values())


def _strip_noise(text: str) -> str:
    return _COMMENT_LINE.sub("", _PSQL_COMMAND.sub("", text))


def _parse_statements(text: str) -> list[exp.Expr]:
    """Parse the dump, and keep going when one statement will not read.

    A dump can carry statements we have no interest in -- grants, ownership
    changes, sequences -- and a single one of them failing to parse should not
    cost the user the tables. So a whole-file parse that fails is retried one
    statement at a time, and the statements that still will not read are
    dropped.
    """
    try:
        return [s for s in sqlglot.parse(text, dialect="postgres") if s is not None]
    except SqlglotError:
        statements: list[exp.Expr] = []
        for chunk in text.split(";\n"):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                parsed = sqlglot.parse_one(chunk, dialect="postgres")
            except SqlglotError:
                continue
            if parsed is not None:
                statements.append(parsed)
        return statements


def _table_from_create(
    create: exp.Create, schema_name: str | None
) -> RawTable | None:
    schema = create.this
    if not isinstance(schema, exp.Schema):
        return None

    table_expression = schema.this
    if not isinstance(table_expression, exp.Table):
        return None

    name, qualifier = _table_name(table_expression)
    if schema_name and qualifier and qualifier != schema_name:
        return None

    table = RawTable(name=name)
    for node in schema.expressions:
        if isinstance(node, exp.ColumnDef):
            table.columns.append(_column(node))
        elif isinstance(node, exp.Constraint):
            _apply_constraints(table, node.expressions)
    return table


def _apply_alter(
    alter: exp.Alter,
    tables: dict[str, RawTable],
    pending: dict[str, list[exp.Expr]],
    schema_name: str | None,
) -> None:
    if alter.args.get("kind") != "TABLE":
        return

    table_expression = alter.this
    if not isinstance(table_expression, exp.Table):
        return

    name, qualifier = _table_name(table_expression)
    if schema_name and qualifier and qualifier != schema_name:
        return

    constraints: list[exp.Expr] = []
    for action in alter.args.get("actions") or []:
        if not isinstance(action, exp.AddConstraint):
            continue
        for constraint in action.expressions:
            if isinstance(constraint, exp.Constraint):
                constraints.extend(constraint.expressions)
            else:
                constraints.append(constraint)

    if not constraints:
        return

    table = tables.get(name)
    if table is None:
        # The constraint arrived before the table did. Hold on to it rather
        # than dropping it, because the order of statements in a dump is not
        # something to rely on.
        pending.setdefault(name, []).extend(constraints)
        return

    _apply_constraints(table, constraints)


def _apply_unique_index(
    create: exp.Create, tables: dict[str, RawTable], schema_name: str | None
) -> None:
    """A unique index is a unique constraint as far as a schema is concerned.

    The live reader sees both through `pg_index`, so the dump reader has to
    see both too, or the two documents disagree about a table that is
    perfectly ordinary.
    """
    if not create.args.get("unique"):
        return

    index = create.this
    if not isinstance(index, exp.Index):
        return

    table_expression = index.args.get("table")
    if not isinstance(table_expression, exp.Table):
        return

    name, qualifier = _table_name(table_expression)
    if schema_name and qualifier and qualifier != schema_name:
        return

    table = tables.get(name)
    if table is None:
        return

    columns = _index_columns(index)
    if columns:
        table.unique.append(columns)


def _apply_constraints(table: RawTable, expressions: list[exp.Expr]) -> None:
    for node in expressions:
        if isinstance(node, exp.PrimaryKey):
            table.primary_key = _column_names(node)
        elif isinstance(node, exp.UniqueColumnConstraint):
            columns = _column_names(node)
            if columns:
                table.unique.append(columns)
        elif isinstance(node, exp.ForeignKey):
            foreign_key = _foreign_key(node)
            if foreign_key is not None:
                table.foreign_keys.append(foreign_key)


def _column(column_definition: exp.ColumnDef) -> RawColumn:
    constraints = column_definition.args.get("constraints") or []
    nullable = not any(
        isinstance(constraint.args.get("kind"), exp.NotNullColumnConstraint)
        for constraint in constraints
    )
    return RawColumn(
        name=column_definition.this.name,
        type=_type_name(column_definition.args.get("kind")),
        nullable=nullable,
    )


def _column_names(node: exp.Expr) -> list[str]:
    """The columns a key names, whichever shape it arrived in.

    A primary key keeps them directly in `expressions`; a unique constraint
    wraps them in a `Schema`. Both shapes turn up in the same dump.
    """
    if isinstance(node, exp.UniqueColumnConstraint):
        inner = node.args.get("this")
        parts = list(inner.expressions) if isinstance(inner, exp.Schema) else []
    else:
        parts = list(node.expressions or [])

    names: list[str] = []
    for part in parts:
        if isinstance(part, exp.ColumnDef):
            names.append(part.this.name)
        elif isinstance(part, exp.Identifier):
            names.append(part.name)
    return names


def _foreign_key(node: exp.ForeignKey) -> RawForeignKey | None:
    reference = node.args.get("reference")
    if not isinstance(reference, exp.Reference):
        return None

    target = reference.args.get("this")
    if not isinstance(target, exp.Schema):
        return None

    table_expression = target.this
    if not isinstance(table_expression, exp.Table):
        return None

    referenced_columns = [
        column.name for column in target.expressions if isinstance(column, exp.Identifier)
    ]
    if not referenced_columns:
        return None

    columns = [
        column.name for column in node.expressions if isinstance(column, exp.Identifier)
    ]
    if not columns:
        return None

    return RawForeignKey(
        columns=columns,
        references=",".join(
            f"{table_expression.name}.{column}" for column in referenced_columns
        ),
        on_delete=_delete_rule(reference),
    )


def _delete_rule(reference: exp.Reference) -> str | None:
    """The `ON DELETE` rule, or `None` when there is not one.

    `NO ACTION` is the absence of a rule rather than a rule of its own: a dump
    does not write it, and the live reader reports it as a code that means
    "nothing". Both are reported here as nothing, so the two readers agree.
    """
    for option in reference.args.get("options") or []:
        text = option if isinstance(option, str) else option.sql(dialect="postgres")
        upper = text.strip().upper()
        if upper.startswith("ON DELETE "):
            rule = upper[len("ON DELETE ") :].strip()
            return None if rule == "NO ACTION" else rule
    return None


def _index_columns(index: exp.Index) -> list[str]:
    params = index.args.get("params")
    columns: list[str] = []
    for ordered in (params.args.get("columns") or []) if params else []:
        column = ordered.this if isinstance(ordered, exp.Ordered) else ordered
        if isinstance(column, exp.Column):
            column = column.this
        if isinstance(column, exp.Identifier):
            columns.append(column.name)
    return columns


def _type_name(kind: exp.Expr | None) -> str:
    if kind is None:
        return "unknown"
    if not isinstance(kind, exp.DataType):
        return kind.sql(dialect="postgres")

    base = str(kind.this.name).lower()

    if base == "array":
        inner = kind.args.get("expressions") or []
        return f"{_type_name(inner[0])}[]" if inner else "unknown[]"

    if base == "userdefined":
        return _user_defined_name(kind)

    name = _TYPE_NAMES.get(base, base)
    parameters = kind.args.get("expressions")
    if parameters:
        # No space after the comma: `format_type` prints `numeric(10,2)`, and
        # the two readers have to spell a type the same way.
        rendered = ",".join(
            parameter.this.name for parameter in parameters if parameter.this
        )
        return f"{name}({rendered})" if rendered else name
    return name


def _user_defined_name(kind: exp.DataType) -> str:
    inner = kind.args.get("kind")
    if isinstance(inner, exp.Dot):
        return f"{inner.this.name}.{inner.expression.name}"
    if isinstance(inner, exp.Identifier):
        return inner.name
    return "unknown"


def _table_name(table_expression: exp.Table) -> tuple[str, str | None]:
    qualifier = table_expression.args.get("db")
    return (
        table_expression.name,
        qualifier.name if isinstance(qualifier, exp.Identifier) else None,
    )
