"""add schema columns to projects

Revision ID: c4a91f2b7d55
Revises: 3f4fcefa0e86
Create Date: 2026-10-24 14:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c4a91f2b7d55'
down_revision: Union[str, Sequence[str], None] = '3f4fcefa0e86'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('projects',
    sa.Column('schema_doc', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('projects',
    sa.Column('schema_source', sa.String(length=40), nullable=True))
    op.add_column('projects',
    sa.Column('schema_fetched_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('projects', 'schema_fetched_at')
    op.drop_column('projects', 'schema_source')
    op.drop_column('projects', 'schema_doc')
