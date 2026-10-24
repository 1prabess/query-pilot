from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, unquote, urlsplit

from sqlalchemy import Boolean, DateTime, ForeignKey, String, false, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.errors import NoConnection
from app.db.session import Base
from app.models.project_connection import ProjectConnection

if TYPE_CHECKING:
    from app.models.user import User


def build_dsn(
    *, host: str, port: int, database: str, user: str, password: str
) -> str:
    """Assemble a PostgreSQL URI from fields that have already been validated.

    The password is percent-encoded, and that is not decoration. A password
    containing `@`, `/` or `#` produces a URI that parses into the wrong host
    and the wrong password -- a real bug in most projects that assemble a DSN
    by hand, and invisible until a customer has a punctuation-heavy password and
    reports that their database "doesn't connect".

    Nothing is ever accepted from the client as a DSN string. A raw `dsn: str`
    box is a URL the user controls that a library then interprets, and that
    library's idea of what a DSN may contain is broader than yours.
    """
    return (
        f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}"
        f"@{host}:{port}/{quote(database, safe='')}"
    )


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    writes_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )

    schema_doc: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    schema_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    schema_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    connection: Mapped[ProjectConnection | None] = relationship(
        back_populates="project", cascade="all, delete-orphan", uselist=False
    )
    owner: Mapped[User] = relationship(back_populates="projects")

    def set_connection(
        self,
        *,
        host: str,
        port: int,
        database: str,
        user: str,
        password: str,
        schema_name: str,
    ) -> None:
        """Store or replace this project's credential.

        The DSN handed to `dsn_encrypted` is *plaintext*. It does not need to be
        encrypted here, because the column type does that on the way into the
        database. Encrypting by hand at the call site as well would encrypt it
        twice, and the bug would only show up as "the password is wrong" much
        later.
        """
        dsn = build_dsn(
            host=host, port=port, database=database, user=user, password=password
        )
        if self.connection is None:
            self.connection = ProjectConnection(
                project_id=self.id, dsn_encrypted=dsn, schema_name=schema_name
            )
        else:
            self.connection.dsn_encrypted = dsn
            self.connection.schema_name = schema_name

    def connection_details(self) -> dict:
        """The stored connection fields, minus the password.

        Raises `NoConnection` rather than returning `None`: every caller needs
        these fields, and there is no sensible way to ask a question against a
        project that has no connection.
        """
        if self.connection is None:
            raise NoConnection(
                "This project has no database connection saved yet.")

        parsed = urlsplit(self.connection.dsn_encrypted)
        return {
            "host": parsed.hostname or "",
            "port": parsed.port or 5432,
            "database": unquote(parsed.path.lstrip("/")),
            "user": unquote(parsed.username or ""),
            "schema_name": self.connection.schema_name,
        }

    def decrypted_dsn(self) -> str:
        """The customer's connection string, ready to hand to a driver."""
        if self.connection is None:
            raise NoConnection(
                "This project has no database connection saved yet.")
        return self.connection.dsn_encrypted
