"""add revoked_at to users

Revision ID: c7e1a4b92f60
Revises: db0f05d444dc
Create Date: 2026-10-26 09:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c7e1a4b92f60'
down_revision: Union[str, Sequence[str], None] = 'db0f05d444dc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users',
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users',
    sa.Column('revoked_by', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('users',
    sa.Column('revoked_reason', sa.Text(), nullable=True))
    # SET NULL, not CASCADE: deleting the administrator must not delete the
    # account they called on.
    op.create_foreign_key('fk_users_revoked_by', 'users', 'users',
    ['revoked_by'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_users_revoked_by', 'users', type_='foreignkey')
    op.drop_column('users', 'revoked_reason')
    op.drop_column('users', 'revoked_by')
    op.drop_column('users', 'revoked_at')
