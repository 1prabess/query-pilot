"""give the schema tables a server-side id, and backfill a baseline version

Revision ID: b2e9d47a1c06
Revises: f3c81a9d24be
Create Date: 2026-10-26 10:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2e9d47a1c06'
down_revision: Union[str, Sequence[str], None] = 'f3c81a9d24be'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # The models say the server generates these ids; the tables did not say
    # so, so anything that inserts without going through the ORM fails.
    op.alter_column(
        'schema_versions',
        'id',
        server_default=sa.text('gen_random_uuid()'),
        existing_type=sa.UUID(),
    )
    op.alter_column(
        'schema_changes',
        'id',
        server_default=sa.text('gen_random_uuid()'),
        existing_type=sa.UUID(),
    )

    # A project that had a schema before the tracking existed has nothing to
    # compare against. Give it the version it was already on, so its history
    # starts from the state we knew about rather than from empty.
    op.execute(
        """
        INSERT INTO schema_versions
            (project_id, version, captured_at, captured_by, source, document, change_count)
        SELECT
            p.id,
            1,
            COALESCE(p.schema_fetched_at, NOW()),
            NULL,
            COALESCE(p.schema_source, 'introspection'),
            p.schema_doc,
            0
        FROM projects p
        WHERE p.schema_doc IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM schema_versions v WHERE v.project_id = p.id
          )
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        """
        DELETE FROM schema_versions
        WHERE version = 1
          AND change_count = 0
          AND captured_by IS NULL
        """
    )
    op.alter_column(
        'schema_changes',
        'id',
        server_default=None,
        existing_type=sa.UUID(),
    )
    op.alter_column(
        'schema_versions',
        'id',
        server_default=None,
        existing_type=sa.UUID(),
    )
