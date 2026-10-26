from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, false, func, true
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.subscription import Subscription


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )
    email: Mapped[str] = mapped_column(CITEXT, unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(
        String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )
    email_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false())
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=true())
    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="user"
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )
    revoked_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    revoked_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    # Named as a registry string, not imported: Project imports this module,
    # so a runtime import of Project here would be circular.
    projects: Mapped[list["Project"]] = relationship(
        "Project", back_populates="owner")
    subscription: Mapped["Subscription | None"] = relationship(
        "Subscription", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )

    @property
    def is_user_active(self) -> bool:
        return self.revoked_at is None
