"""What changed between two states of a schema.

Two documents in, a list of changes out. It never touches a database, which
is what makes it possible to check the diff against a pair of documents
without a customer connection anywhere near it.

The documents are the ones `schema_document` builds, so both readers of a
schema -- a dump and a live introspection -- can be compared with each other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# The kinds of change a schema can undergo.
TABLE_ADDED = "table_added"
TABLE_REMOVED = "table_removed"
COLUMN_ADDED = "column_added"
COLUMN_REMOVED = "column_removed"
COLUMN_TYPE_CHANGED = "column_type_changed"
COLUMN_NULLABILITY_CHANGED = "column_nullability_changed"
PRIMARY_KEY_CHANGED = "primary_key_changed"
UNIQUE_ADDED = "unique_added"
UNIQUE_REMOVED = "unique_removed"
FOREIGN_KEY_ADDED = "foreign_key_added"
FOREIGN_KEY_REMOVED = "foreign_key_removed"
FOREIGN_KEY_CHANGED = "foreign_key_changed"

# Whether a change can break something that used to work.
ADDITIVE = "additive"
BREAKING = "breaking"


@dataclass(frozen=True)
class SchemaDifference:
    """One difference, and what it does to the queries that relied on it."""

    kind: str
    severity: str
    table: str
    column: str | None = None
    detail: str | None = None


def summarize(
    kind: str, table: str, column: str | None, detail: str | None
) -> str:
    """One line describing a change, for a person to read.

    The kind and the severity are tokens the code works with; this is the
    same change said the way it would be said out loud.
    """
    detail = detail or ""

    if kind == TABLE_ADDED:
        return f"Added table {table} ({detail})"
    if kind == TABLE_REMOVED:
        return f"Dropped table {table} ({detail})"
    if kind == COLUMN_ADDED:
        return f"Added column {table}.{column} ({detail})"
    if kind == COLUMN_REMOVED:
        return f"Dropped column {table}.{column} ({detail})"
    if kind == COLUMN_TYPE_CHANGED:
        return f"Changed the type of {table}.{column} ({detail})"
    if kind == COLUMN_NULLABILITY_CHANGED:
        return f"{table}.{column} is {detail}"
    if kind == PRIMARY_KEY_CHANGED:
        return f"Changed the primary key of {table} ({detail})"
    if kind == UNIQUE_ADDED:
        return f"Added a unique constraint on {table} {detail}"
    if kind == UNIQUE_REMOVED:
        return f"Dropped a unique constraint on {table} {detail}"
    if kind == FOREIGN_KEY_ADDED:
        return f"Added a foreign key on {table} ({detail})"
    if kind == FOREIGN_KEY_REMOVED:
        return f"Dropped a foreign key on {table} ({detail})"
    if kind == FOREIGN_KEY_CHANGED:
        return f"Changed a foreign key on {table} ({detail})"
    return f"{kind} on {table}"


def diff_documents(
    old: dict[str, Any] | None, new: dict[str, Any]
) -> list[SchemaDifference]:
    """Every difference between two documents.

    A first document has nothing to be compared against, so it has no
    changes: everything in it is new, and saying so would bury the changes
    that come later.

    Keys are only compared when both documents came from the same reader. A
    dump and a live introspection do not see a database through quite the
    same lens, and a key the dump happened to leave out is not a key the
    customer dropped.
    """
    if old is None:
        return []

    old_tables = _tables(old)
    new_tables = _tables(new)
    compare_keys = old.get("source") == new.get("source")

    changes: list[SchemaDifference] = []

    for name in sorted(new_tables):
        if name not in old_tables:
            changes.append(
                SchemaDifference(TABLE_ADDED, ADDITIVE, name, detail=_table_detail(new_tables[name]))
            )

    for name in sorted(old_tables):
        if name not in new_tables:
            changes.append(
                SchemaDifference(TABLE_REMOVED, BREAKING, name, detail=_table_detail(old_tables[name]))
            )

    for name in sorted(set(old_tables) & set(new_tables)):
        changes.extend(_diff_table(old_tables[name], new_tables[name], compare_keys))

    return changes


def _tables(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {table["name"]: table for table in document.get("tables") or []}


def _diff_table(
    old: dict[str, Any], new: dict[str, Any], compare_keys: bool = True
) -> list[SchemaDifference]:
    changes: list[SchemaDifference] = []
    name = new["name"]

    old_columns = {column["name"]: column for column in old.get("columns") or []}
    new_columns = {column["name"]: column for column in new.get("columns") or []}

    for column in sorted(set(new_columns) - set(old_columns)):
        changes.append(
            SchemaDifference(
                COLUMN_ADDED,
                ADDITIVE,
                name,
                column=column,
                detail=_column_detail(new_columns[column]),
            )
        )

    for column in sorted(set(old_columns) - set(new_columns)):
        changes.append(
            SchemaDifference(
                COLUMN_REMOVED,
                BREAKING,
                name,
                column=column,
                detail=_column_detail(old_columns[column]),
            )
        )

    for column in sorted(set(old_columns) & set(new_columns)):
        changes.extend(_diff_column(name, old_columns[column], new_columns[column]))

    if (old.get("primary_key") or []) != (new.get("primary_key") or []):
        changes.append(
            SchemaDifference(
                PRIMARY_KEY_CHANGED,
                BREAKING,
                name,
                column=_single_column(tuple(old.get("primary_key") or [])),
                detail=(
                    f"{', '.join(old.get('primary_key') or []) or 'none'} -> "
                    f"{', '.join(new.get('primary_key') or []) or 'none'}"
                ),
            )
        )

    changes.extend(_diff_unique(name, old, new) if compare_keys else [])
    changes.extend(_diff_foreign_keys(name, old, new) if compare_keys else [])
    return changes


def _diff_column(
    name: str, old: dict[str, Any], new: dict[str, Any]
) -> list[SchemaDifference]:
    changes: list[SchemaDifference] = []
    column = new["name"]

    if old["type"] != new["type"]:
        changes.append(
            SchemaDifference(
                COLUMN_TYPE_CHANGED,
                BREAKING,
                name,
                column=column,
                detail=f"{old['type']} -> {new['type']}",
            )
        )

    if old["nullable"] != new["nullable"]:
        changes.append(
            SchemaDifference(
                COLUMN_NULLABILITY_CHANGED,
                ADDITIVE if new["nullable"] else BREAKING,
                name,
                column=column,
                detail="now nullable" if new["nullable"] else "now required",
            )
        )

    return changes


def _diff_unique(
    name: str, old: dict[str, Any], new: dict[str, Any]
) -> list[SchemaDifference]:
    old_unique = {tuple(columns) for columns in old.get("unique") or []}
    new_unique = {tuple(columns) for columns in new.get("unique") or []}

    changes: list[SchemaDifference] = []
    for columns in sorted(new_unique - old_unique):
        changes.append(
            SchemaDifference(
                UNIQUE_ADDED,
                ADDITIVE,
                name,
                column=_single_column(columns),
                detail=f"({', '.join(columns)})",
            )
        )
    for columns in sorted(old_unique - new_unique):
        changes.append(
            SchemaDifference(
                UNIQUE_REMOVED,
                BREAKING,
                name,
                column=_single_column(columns),
                detail=f"({', '.join(columns)})",
            )
        )
    return changes


def _diff_foreign_keys(
    name: str, old: dict[str, Any], new: dict[str, Any]
) -> list[SchemaDifference]:
    old_keys = _foreign_keys(old)
    new_keys = _foreign_keys(new)

    changes: list[SchemaDifference] = []
    for columns in sorted(set(new_keys) - set(old_keys)):
        changes.append(
            SchemaDifference(
                FOREIGN_KEY_ADDED,
                ADDITIVE,
                name,
                column=_single_column(columns),
                detail=_foreign_key_detail(new_keys[columns]),
            )
        )
    for columns in sorted(set(old_keys) - set(new_keys)):
        changes.append(
            SchemaDifference(
                FOREIGN_KEY_REMOVED,
                BREAKING,
                name,
                column=_single_column(columns),
                detail=_foreign_key_detail(old_keys[columns]),
            )
        )
    for columns in sorted(set(old_keys) & set(new_keys)):
        if old_keys[columns] != new_keys[columns]:
            changes.append(
                SchemaDifference(
                    FOREIGN_KEY_CHANGED,
                    BREAKING,
                    name,
                    column=_single_column(columns),
                    detail=(
                        f"{_foreign_key_detail(old_keys[columns])} -> "
                        f"{_foreign_key_detail(new_keys[columns])}"
                    ),
                )
            )
    return changes


def _foreign_keys(table: dict[str, Any]) -> dict[tuple[str, ...], dict[str, Any]]:
    return {
        tuple(foreign_key["columns"]): foreign_key
        for foreign_key in table.get("foreign_keys") or []
    }


def _foreign_key_detail(foreign_key: dict[str, Any]) -> str:
    detail = f"{', '.join(foreign_key['columns'])} -> {foreign_key['references']}"
    if foreign_key.get("on_delete"):
        detail += f" on delete {foreign_key['on_delete']}"
    return detail


def _table_detail(table: dict[str, Any]) -> str:
    columns = table.get("columns") or []
    count = len(columns)
    return f"{count} column" if count == 1 else f"{count} columns"


def _column_detail(column: dict[str, Any]) -> str:
    nullability = "nullable" if column.get("nullable") else "required"
    return f"{column['type']}, {nullability}"


def _single_column(columns: tuple[str, ...]) -> str | None:
    """The column a constraint names, when it names exactly one.

    A key over several columns belongs to no single one of them, and saying
    it does would point the impact analysis at a column that is not the
    reason the constraint changed.
    """
    return columns[0] if len(columns) == 1 else None
