"""The ER diagram of a schema, worked out from the schema itself.

Everything here is read out of the schema document and nothing else. The
tables are the entities, the columns are the attributes, the foreign keys
are the relationships, and the keys and the nullability say how many of
each end there are. Nothing is asked of a model, and nothing is guessed:
the same schema always produces the same diagram.

What the schema cannot say -- what a relationship is called, that a column
is worked out rather than stored, that one table is a kind of another -- is
a separate business, and `apply_annotations` puts it on top of this.
"""

from __future__ import annotations

import re

from app.schemas.er import ErAttribute, ErEnd, ErEntity, ErModel, ErRelationship

# The words that look wrong in title case.
_ACRONYMS = {
    "id": "ID",
    "ids": "IDs",
    "url": "URL",
    "api": "API",
    "sql": "SQL",
    "uuid": "UUID",
    "uri": "URI",
    "ip": "IP",
}

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SEPARATORS = re.compile(r"[_\-\s]+")


def build_model(schema_doc: dict) -> ErModel:
    """The diagram of a schema document.

    A table that does nothing but hold a relationship is not drawn as a box
    with attributes; it is drawn as the relationship it holds. The table is
    still reported in `folded_tables`, because a table that is not on the
    diagram should not be a table that has quietly vanished.
    """
    tables = _tables(schema_doc)
    associative = _associative_tables(tables)

    entities = [
        _entity(name, tables[name])
        for name in sorted(tables)
        if name not in associative
    ]

    relationships: list[ErRelationship] = []
    for name in sorted(tables):
        if name in associative:
            relationships.extend(_through(name, tables[name], tables))
            continue
        for index, foreign_key in enumerate(tables[name].get("foreign_keys") or []):
            relationships.append(_relationship(name, tables[name], foreign_key, tables))

    return ErModel(
        entities=entities,
        relationships=relationships,
        folded_tables=sorted(associative),
    )


def apply_annotations(model: ErModel, annotations: dict) -> ErModel:
    """Put what the model said on top of what the schema says.

    Only names the schema already has are taken. A relationship label for a
    relationship that is not in the diagram is discarded, and an attribute
    that is marked derived when the entity has no such attribute is
    discarded too. The schema is the answer key, and a wrong answer to a
    question with an answer key is not worth having.
    """
    labels = annotations.get("labels")
    if not isinstance(labels, dict):
        labels = {}

    entity_labels = labels.get("entities")
    entity_labels = entity_labels if isinstance(entity_labels, dict) else {}
    relationship_labels = labels.get("relationships")
    relationship_labels = relationship_labels if isinstance(relationship_labels, dict) else {}

    derived = annotations.get("derived_attributes")
    derived = derived if isinstance(derived, dict) else {}

    for entity in model.entities:
        label = entity_labels.get(entity.id)
        if isinstance(label, str) and label.strip():
            entity.label = label.strip()

        marked = derived.get(entity.id)
        if not isinstance(marked, list):
            continue
        wanted = {name for name in marked if isinstance(name, str)}
        for attribute in entity.attributes:
            if attribute.name in wanted:
                attribute.derived = True

    for relationship in model.relationships:
        label = relationship_labels.get(relationship.id)
        if isinstance(label, str) and label.strip():
            relationship.label = label.strip()

    subtypes = annotations.get("subtypes")
    if isinstance(subtypes, list):
        _apply_subtypes(model, subtypes)

    return model


def _apply_subtypes(model: ErModel, subtypes: list) -> None:
    """Turn a relationship the model called a subtype into one.

    A subtype is drawn differently: the child is not a box with a key of its
    own but a kind of the parent, and the arrow between them points the
    other way.
    """
    by_id = {entity.id: entity for entity in model.entities}

    for entry in subtypes:
        if not isinstance(entry, dict):
            continue
        child = entry.get("child")
        parent = entry.get("parent")
        if not isinstance(child, str) or not isinstance(parent, str):
            continue
        if child not in by_id or parent not in by_id:
            continue

        for relationship in model.relationships:
            if relationship.child.entity != child or relationship.parent.entity != parent:
                continue
            relationship.kind = "isa"
            by_id[child].weak = False
            for attribute in by_id[child].attributes:
                attribute.partial_key = False
            break


def _tables(schema_doc: dict) -> dict[str, dict]:
    return {table["name"]: table for table in schema_doc.get("tables") or []}


def _entity(name: str, table: dict) -> ErEntity:
    """One table as an entity, with its columns as attributes."""
    primary = list(table.get("primary_key") or [])
    unique = {tuple(columns) for columns in table.get("unique") or []}
    foreign_columns = {
        column
        for foreign_key in table.get("foreign_keys") or []
        for column in foreign_key["columns"]
    }

    # A weak entity has no key of its own. Its key is borrowed from the
    # entity it belongs to, and one part of the key is still its own.
    borrowed = {column for column in primary if column in foreign_columns}
    weak = bool(borrowed) and len(primary) > 1

    attributes = []
    for column in table.get("columns") or []:
        column_name = column["name"]
        # The borrowed part of the key is not underlined here. The owner
        # entity supplies it and the identifying relationship says so, so
        # underlining it as well would state the key twice -- and would read
        # as if the weak entity had a key of its own.
        inherited = weak and column_name in borrowed
        # What is left of the key is the discriminator, and a discriminator
        # is a partial key rather than a key: the entity is only partly
        # identified by it.
        partial = weak and column_name in primary and not inherited
        attributes.append(
            ErAttribute(
                id=f"{name}.{column_name}",
                name=column_name,
                label=_humanize(column_name),
                type=column["type"],
                key=(
                    column_name in primary or (column_name,) in unique
                )
                and not inherited
                and not partial,
                partial_key=partial,
                multivalued=column["type"].endswith("[]"),
                nullable=bool(column.get("nullable")),
            )
        )

    return ErEntity(
        id=f"table:{name}",
        table=name,
        label=_humanize(name),
        weak=weak,
        attributes=attributes,
    )


def _relationship(
    name: str, table: dict, foreign_key: dict, tables: dict[str, dict]
) -> ErRelationship:
    """One foreign key as a relationship.

    The side holding the key is the many side, unless the key is also
    unique -- in which case one row of the parent has at most one row of
    the child, and the relationship is one to one.
    """
    columns = list(foreign_key["columns"])
    parent_table = _parent_table(foreign_key)

    unique = {tuple(entry) for entry in table.get("unique") or []}
    primary = tuple(table.get("primary_key") or [])
    is_unique = tuple(columns) in unique or tuple(columns) == primary

    nullable = any(
        _column(table, column).get("nullable", False) for column in columns
    )

    if is_unique:
        child_cardinality = "0..1" if nullable else "1"
    else:
        child_cardinality = "N"

    # The relationship identifies the child when the key is part of the
    # child's key but is not the whole of it: the child is identified by
    # what it borrows plus what it has of its own.
    identifying = set(columns) < set(primary)

    return ErRelationship(
        id=f"fk:{name}.{'+'.join(columns)}",
        label=_humanize(parent_table),
        identifying=identifying,
        child=ErEnd(
            entity=f"table:{name}",
            cardinality=child_cardinality,
            role=_role_label(columns),
        ),
        parent=ErEnd(entity=f"table:{parent_table}", cardinality="1"),
    )


def _associative_tables(tables: dict[str, dict]) -> dict[str, dict]:
    """The tables that exist only to hold a relationship.

    A table qualifies when it points at exactly two other tables, holds
    nothing but those two keys and its own, and has nothing else in it. A
    table with a third column of its own -- a date, a note, a quantity --
    is a table with something to say, and is drawn as one.
    """
    associative: dict[str, dict] = {}

    for name, table in tables.items():
        foreign_keys = table.get("foreign_keys") or []
        if len(foreign_keys) != 2:
            continue

        parents = {_parent_table(foreign_key) for foreign_key in foreign_keys}
        if len(parents) != 2 or name in parents:
            continue

        key_columns = {column for foreign_key in foreign_keys for column in foreign_key["columns"]}
        primary = set(table.get("primary_key") or [])
        extra = {column["name"] for column in table.get("columns") or []} - key_columns - primary
        if extra:
            continue

        # A key that may be absent does not make a relationship: it makes a
        # row that belongs to nobody.
        if any(
            _column(table, column).get("nullable", False)
            for foreign_key in foreign_keys
            for column in foreign_key["columns"]
        ):
            continue

        associative[name] = table

    return associative


def _through(name: str, table: dict, tables: dict[str, dict]) -> list[ErRelationship]:
    """The many-to-many a junction table was holding."""
    foreign_keys = table.get("foreign_keys") or []
    if len(foreign_keys) != 2:
        return []

    ends = []
    for foreign_key in foreign_keys:
        parent_table = _parent_table(foreign_key)
        if parent_table not in tables:
            return []
        ends.append(
            (
                foreign_key,
                ErEnd(
                    entity=f"table:{parent_table}",
                    cardinality="N",
                    role=_role_label(foreign_key["columns"]),
                ),
            )
        )

    (first_key, first_end), (_, second_end) = ends

    return [
        ErRelationship(
            id=f"fk:{name}.{'+'.join(first_key['columns'])}",
            label=_humanize(_parent_table(first_key)),
            child=first_end,
            parent=second_end,
            via=name,
        )
    ]


def _parent_table(foreign_key: dict) -> str:
    first = foreign_key["references"].split(",")[0]
    return first.split(".")[0].strip()


def _column(table: dict, name: str) -> dict:
    for column in table.get("columns") or []:
        if column["name"] == name:
            return column
    return {}


def _role_label(columns: list[str]) -> str:
    """What the foreign key is called on the child side.

    `account_id` is the role the parent plays in the child, which is what
    crow's foot notation writes on the line.
    """
    if not columns:
        return None
    name = columns[0]
    for suffix in ("_id", "_key", "_fk"):
        if name.endswith(suffix) and len(name) > len(suffix):
            name = name[: -len(suffix)]
            break
    return _humanize(name)


def _humanize(name: str) -> str:
    """`character_instantiation` -> `Character Instantiation`."""
    if not name:
        return name
    spaced = _CAMEL_BOUNDARY.sub(" ", name)
    words = [word for word in _SEPARATORS.split(spaced) if word]
    return " ".join(_ACRONYMS.get(word.lower(), word.capitalize()) for word in words)
