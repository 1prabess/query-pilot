"""add schema versions and changes

Revision ID: f3c81a9d24be
Revises: c7e1a4b92f60
Create Date: 2026-10-26 09:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f3c81a9d24be'
down_revision: Union[str, Sequence[str], None] = 'c7e1a4b92f60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'schema_versions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('project_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('captured_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('captured_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('source', sa.String(length=40), nullable=False),
        sa.Column('document', postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False),
        sa.Column('change_count', sa.Integer(),
                  server_default=sa.text('0'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['captured_by'], ['users.id'],
                                ondelete='SET NULL'),
        sa.UniqueConstraint('project_id', 'version')
    )
    op.create_index(op.f('ix_schema_versions_project_id'),
                    'schema_versions', ['project_id'], unique=False)

    op.create_table(
        'schema_changes',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('project_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=40), nullable=False),
        sa.Column('severity', sa.String(length=20), nullable=False),
        sa.Column('table_name', sa.String(length=255), nullable=False),
        sa.Column('column_name', sa.String(length=255), nullable=True),
        sa.Column('detail', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'],
                                ondelete='CASCADE')
    )
    op.create_index(op.f('ix_schema_changes_project_id'),
                    'schema_changes', ['project_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_schema_changes_project_id'),
                  table_name='schema_changes')
    op.drop_table('schema_changes')

    op.drop_index(op.f('ix_schema_versions_project_id'),
                  table_name='schema_versions')
    op.drop_table('schema_versions')
