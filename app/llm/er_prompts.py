"""The prompt that names the diagram.

The schema says what the tables are and what points at what. It does not
say what a relationship is called, that a column is worked out rather than
stored, or that one table is a kind of another. Those are the things a
person knows and a database does not, and they are what this prompt asks
for.

The questions are asked one small thing at a time, against a schema the
model can see in full. A model asked to describe a whole schema at once
will describe a schema it imagines; a model asked to name four
relationships will name the four it was given.
"""

from __future__ import annotations

_ER_SYSTEM = """You name an entity-relationship diagram of a PostgreSQL schema.

Rules:
- Only use the tables and columns listed below. If something is not there,
  it does not exist.
- A relationship label is how the relationship reads in the business:
  "Has", "Contains", "Places", "Carries". Not the table names, and not
  "customer_id".
- A derived attribute is worked out when it is asked for rather than
  stored. Name a column derived only when it is plainly computed -- a
  total, an age, a count, a display name. If no column is computed, return
  an empty list for that table.
- A subtype is a table that is a kind of another table: it carries the
  other's key and something of its own. Name both by the ids given below.
- Return no prose outside the JSON object."""

_ER_FORMAT = """Answer with JSON only:
{
  "labels": {
    "entities": {"table:orders": "Orders"},
    "relationships": {"fk:orders.customer_id": "Places"}
  },
  "derived_attributes": {"table:orders": ["total_with_tax"]},
  "subtypes": [
    {"child": "table:creep_instantiation", "parent": "table:character", "label": "IsType"}
  ]
}

Give a label for every entity and every relationship listed below."""


def build_er_prompt(schema_doc: dict) -> tuple[str, str]:
    """The (system, user) pair for the labelling call.

    The schema is rendered the same way the SQL prompt renders it, so the
    two calls describe a database in the same words.
    """
    return _ER_SYSTEM, f"{_render(schema_doc)}\n\n{_ER_FORMAT}"


def _render(schema_doc: dict) -> str:
    """The tables, as the model reads them.

    Only the structural keys are read. A schema document holds nothing but
    structure, but a customer could put anything in one, and the diagram
    prompt is no place for it to go.
    """
    lines = ["Database schema:"]

    for table in schema_doc.get("tables") or []:
        lines.append(f"table {table['name']} (id: table:{table['name']}):")
        primary = set(table.get("primary_key") or [])

        for column in table.get("columns") or []:
            marks = ""
            if column["name"] in primary:
                marks += " PRIMARY KEY"
            if not column.get("nullable", True):
                marks += " NOT NULL"
            if column["type"].endswith("[]"):
                marks += " MULTIVALUED"
            lines.append(f"  {column['name']}: {column['type']}{marks}")

        for foreign_key in table.get("foreign_keys") or []:
            columns = "+".join(foreign_key["columns"])
            lines.append(
                f"  FK({columns}) -> {foreign_key['references']}"
                f" (id: fk:{table['name']}.{columns})"
            )

    return "\n".join(lines)
