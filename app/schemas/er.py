"""The ER diagram, as data.

The diagram is a picture, but the picture is built from this. Nothing in it
is a coordinate: the client lays it out, so one model can be drawn by a
browser, printed, or read by something that only wants to know what the
relationships are.
"""

from typing import Literal

from pydantic import BaseModel

# How many of the far end a relationship points at.
Cardinality = Literal["1", "N", "0..1"]


class ErAttribute(BaseModel):
    """One attribute of an entity.

    `key` is a key attribute. `partial_key` is the half of a weak entity's
    key that is not the foreign key it borrows. `derived` is worked out
    rather than stored. `multivalued` holds more than one value at a time.
    """

    id: str
    name: str
    label: str
    type: str
    key: bool = False
    partial_key: bool = False
    derived: bool = False
    multivalued: bool = False
    nullable: bool = False


class ErEntity(BaseModel):
    """One table, as an entity.

    `weak` says the entity has no key of its own: it is identified by the
    entity it belongs to. `associative` says the table exists only to hold
    a relationship, and is drawn as one.
    """

    id: str
    table: str
    label: str
    weak: bool = False
    associative: bool = False
    attributes: list[ErAttribute] = []


class ErEnd(BaseModel):
    """One end of a relationship, and how many of that end there are."""

    entity: str
    cardinality: Cardinality = "1"
    role: str | None = None


class ErRelationship(BaseModel):
    """One relationship between two entities.

    `child` is the side that holds the foreign key; `parent` is the side it
    points at. `identifying` is a relationship that gives the child its
    identity, which is what a weak entity is built on. `via` names the table
    that carried the relationship for a many-to-many.
    """

    id: str
    label: str
    kind: Literal["binary", "isa"] = "binary"
    identifying: bool = False
    child: ErEnd
    parent: ErEnd
    via: str | None = None


class ErModel(BaseModel):
    """The whole diagram.

    `folded_tables` are the tables that are not drawn: a table that exists
    only to hold a relationship is drawn as the relationship.
    """

    entities: list[ErEntity] = []
    relationships: list[ErRelationship] = []
    folded_tables: list[str] = []
