"""add query execution metadata

Revision ID: b7d4e91a5c26
Revises: a2c9e14f7b83
Create Date: 2026-10-25 10:12:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d4e91a5c26'
down_revision: Union[str, Sequence[str], None] = 'a2c9e14f7b83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('queries',
    sa.Column('executed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('queries',
    sa.Column('row_count', sa.Integer(), nullable=True))
    op.add_column('queries',
    sa.Column('execution_ms', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('queries', 'execution_ms')
    op.drop_column('queries', 'row_count')
    op.drop_column('queries', 'executed_at')
