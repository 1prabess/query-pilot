"""Ask a live database what its schema is.

`pg_catalog`, not `information_schema`. The difference matters the moment a
customer hands us a read-only role: `information_schema` only shows what the
role may *write*, so a role that can only read a database reports none of its
constraints, and the schema we store is a schema with no keys in it. The
catalog is not filtered that way, so a read-only reader and a read-write reader
see the same database.

The customer's database is not ours. Nothing here writes, and the connection
is opened, asked, and closed.
"""

from __future__ import annotations

import asyncpg

from app.services.schema_document import RawColumn, RawForeignKey, RawTable

COLUMNS_SQL = """
SELECT c.relname AS table_name,
       a.attname AS column_name,
       format_type(a.atttypid, a.atttypmod) AS data_type,
       NOT a.attnotnull AS nullable
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid
WHERE c.relkind IN ('r', 'p')
  AND n.nspname = $1
  AND a.attnum > 0
  AND NOT a.attisdropped
ORDER BY c.relname, a.attnum
"""

PRIMARY_KEYS_SQL = """
SELECT c.relname AS table_name,
       array_agg(a.attname ORDER BY k.ord) AS columns
FROM pg_index i
JOIN pg_class c ON c.oid = i.indrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
WHERE i.indisprimary
  AND n.nspname = $1
  AND k.attnum > 0
GROUP BY c.relname
"""

UNIQUE_SQL = """
SELECT c.relname AS table_name,
       array_agg(a.attname ORDER BY k.ord) AS columns
FROM pg_index i
JOIN pg_class c ON c.oid = i.indrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) ON TRUE
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
WHERE i.indisunique
  AND NOT i.indisprimary
  AND n.nspname = $1
  AND k.attnum > 0
GROUP BY c.relname, i.indexrelid
"""

FOREIGN_KEYS_SQL = """
SELECT c.relname AS table_name,
       fk.relname AS foreign_table,
       con.confdeltype AS delete_rule,
       array_agg(a.attname ORDER BY ord.ord) AS columns,
       array_agg(fa.attname ORDER BY ord.ord) AS foreign_columns
FROM pg_constraint con
JOIN pg_class c ON c.oid = con.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_class fk ON fk.oid = con.confrelid
JOIN ROWS FROM (unnest(con.conkey), unnest(con.confkey))
     WITH ORDINALITY AS ord(conkey, confkey, ord) ON TRUE
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ord.conkey
JOIN pg_attribute fa ON fa.attrelid = fk.oid AND fa.attnum = ord.confkey
WHERE con.contype = 'f'
  AND n.nspname = $1
GROUP BY c.relname, con.oid, fk.relname, con.confdeltype
"""

# What `pg_constraint.confdeltype` means, and which of them count as "a rule".
# `a` is NO ACTION, which is what a foreign key has when it says nothing, so
# it is reported as the absence of a rule.
DELETE_RULES = {
    "a": None,
    "r": "RESTRICT",
    "c": "CASCADE",
    "n": "SET NULL",
    "d": "SET DEFAULT",
}


class IntrospectionError(Exception):
    """We could not read the customer's database.

    Not an `AppError`: the service turns this into the error the API reports.
    """


async def introspect_schema(dsn: str, schema_name: str) -> list[RawTable]:
    """Every table in `schema_name`, with its columns, keys and constraints."""
    try:
        connection = await asyncpg.connect(dsn)
    except Exception as exc:
        raise IntrospectionError(str(exc)) from exc

    try:
        columns = await connection.fetch(COLUMNS_SQL, schema_name)
        primary_keys = await connection.fetch(PRIMARY_KEYS_SQL, schema_name)
        uniques = await connection.fetch(UNIQUE_SQL, schema_name)
        foreign_keys = await connection.fetch(FOREIGN_KEYS_SQL, schema_name)
    except Exception as exc:
        raise IntrospectionError(str(exc)) from exc
    finally:
        await connection.close()

    tables: dict[str, RawTable] = {}

    for row in columns:
        table = tables.setdefault(row["table_name"], RawTable(name=row["table_name"]))
        table.columns.append(
            RawColumn(
                name=row["column_name"],
                type=row["data_type"],
                nullable=row["nullable"],
            )
        )

    for row in primary_keys:
        table = tables.get(row["table_name"])
        if table is not None:
            table.primary_key = list(row["columns"])

    for row in uniques:
        table = tables.get(row["table_name"])
        if table is not None:
            table.unique.append(list(row["columns"]))

    for row in foreign_keys:
        table = tables.get(row["table_name"])
        if table is None:
            continue
        table.foreign_keys.append(
            RawForeignKey(
                columns=list(row["columns"]),
                references=",".join(
                    f"{row['foreign_table']}.{column}"
                    for column in row["foreign_columns"]
                ),
                on_delete=_delete_rule(row["delete_rule"]),
            )
        )

    return list(tables.values())


def _delete_rule(code: bytes | str | None) -> str | None:
    if code is None:
        return None
    if isinstance(code, bytes):
        code = code.decode()
    return DELETE_RULES.get(code)
