

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.crypto import EncryptedString
from app.db.session import Base

if TYPE_CHECKING:
    from app.models.project import Project


class ProjectConnection(Base):
    """The credential, in its own table so listing projects touches no ciphertext."""

    __tablename__ = "project_connections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # `EncryptedString`, not `LargeBinary`. The type is the encryption.
    dsn_encrypted: Mapped[str] = mapped_column(EncryptedString, nullable=False)

    # The schema is a namespace name, not a secret. It stays in the clear so
    # that reading the connection back does not have to parse it out of a URI.
    schema_name: Mapped[str] = mapped_column(
        String(63), default="public", server_default=text("'public'")
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    project: Mapped["Project"] = relationship(back_populates="connection")
