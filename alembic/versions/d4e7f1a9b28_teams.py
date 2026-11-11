"""teams

Projects and plans belong to a team rather than to a person, so that a
group of people share one plan and one set of projects between them.

Revision ID: d4e7f1a9b28
Revises: c5a83f0e7d14
Create Date: 2026-10-27 09:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd4e7f1a9b28'
down_revision: Union[str, Sequence[str], None] = 'c5a83f0e7d14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'teams',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'team_memberships',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('team_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('role', sa.String(length=20), server_default='member', nullable=False),
        sa.Column('can_write', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('team_id', 'user_id'),
    )
    op.create_index(op.f('ix_team_memberships_team_id'), 'team_memberships', ['team_id'], unique=False)
    op.create_index(op.f('ix_team_memberships_user_id'), 'team_memberships', ['user_id'], unique=False)

    op.create_table(
        'team_invitations',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('team_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('email', postgresql.CITEXT(), nullable=False),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('can_write', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('token_hash', sa.LargeBinary(), nullable=False),
        sa.Column('invited_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['invited_by_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_team_invitations_team_id'), 'team_invitations', ['team_id'], unique=False)
    op.create_index('ix_team_invitations_token_hash', 'team_invitations', ['token_hash'], unique=False)

    op.add_column('projects',
        sa.Column('team_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('subscriptions',
        sa.Column('team_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('queries',
        sa.Column('author_id', postgresql.UUID(as_uuid=True), nullable=True))

    # Everybody who already had an account gets a team of their own, with
    # themselves as its owner, so that a project and a plan have somewhere to
    # live for every account that already exists.
    op.execute(
        """
        CREATE TEMPORARY TABLE personal_teams AS
        SELECT gen_random_uuid() AS team_id, id AS user_id, email AS name, created_at
        FROM users
        """
    )
    op.execute(
        """
        INSERT INTO teams (id, name, created_at, updated_at)
        SELECT team_id, name, created_at, now() FROM personal_teams
        """
    )
    op.execute(
        """
        INSERT INTO team_memberships
            (id, team_id, user_id, role, can_write, created_at)
        SELECT gen_random_uuid(), team_id, user_id, 'owner', true, created_at
        FROM personal_teams
        """
    )
    op.execute(
        """
        UPDATE projects p
        SET team_id = pt.team_id
        FROM personal_teams pt
        WHERE pt.user_id = p.owner_id
        """
    )
    op.execute(
        """
        UPDATE subscriptions s
        SET team_id = pt.team_id
        FROM personal_teams pt
        WHERE pt.user_id = s.user_id
        """
    )
    op.execute("DROP TABLE personal_teams")

    op.alter_column('projects', 'team_id',
        existing_type=postgresql.UUID(as_uuid=True), nullable=False)
    op.alter_column('subscriptions', 'team_id',
        existing_type=postgresql.UUID(as_uuid=True), nullable=False)

    op.create_foreign_key('fk_projects_team_id', 'projects', 'teams',
        ['team_id'], ['id'], ondelete='CASCADE')
    op.create_index(op.f('ix_projects_team_id'), 'projects', ['team_id'], unique=False)

    op.create_foreign_key('fk_subscriptions_team_id', 'subscriptions', 'teams',
        ['team_id'], ['id'], ondelete='CASCADE')
    op.drop_constraint('subscriptions_user_id_key', 'subscriptions', type_='unique')
    op.create_unique_constraint('subscriptions_team_id_key', 'subscriptions', ['team_id'])

    op.create_foreign_key('fk_queries_author_id', 'queries', 'users',
        ['author_id'], ['id'], ondelete='SET NULL')
    op.create_index(op.f('ix_queries_author_id'), 'queries', ['author_id'], unique=False)

    op.drop_index(op.f('ix_projects_owner_id'), table_name='projects')
    op.drop_column('projects', 'owner_id')
    op.drop_column('subscriptions', 'user_id')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('projects',
        sa.Column('owner_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('subscriptions',
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True))

    # A project and a plan go back to the person who owns the team that holds
    # them, which is the only person who had them before.
    op.execute(
        """
        UPDATE projects p
        SET owner_id = m.user_id
        FROM team_memberships m
        WHERE m.team_id = p.team_id AND m.role = 'owner'
        """
    )
    op.execute(
        """
        UPDATE subscriptions s
        SET user_id = m.user_id
        FROM team_memberships m
        WHERE m.team_id = s.team_id AND m.role = 'owner'
        """
    )

    op.alter_column('projects', 'owner_id',
        existing_type=postgresql.UUID(as_uuid=True), nullable=False)
    op.alter_column('subscriptions', 'user_id',
        existing_type=postgresql.UUID(as_uuid=True), nullable=False)

    op.create_foreign_key('fk_projects_owner_id', 'projects', 'users',
        ['owner_id'], ['id'], ondelete='CASCADE')
    op.create_index(op.f('ix_projects_owner_id'), 'projects', ['owner_id'], unique=False)

    op.create_foreign_key('fk_subscriptions_user_id', 'subscriptions', 'users',
        ['user_id'], ['id'], ondelete='CASCADE')
    op.drop_constraint('subscriptions_team_id_key', 'subscriptions', type_='unique')
    op.create_unique_constraint('subscriptions_user_id_key', 'subscriptions', ['user_id'])

    op.drop_index(op.f('ix_queries_author_id'), table_name='queries')
    op.drop_constraint('fk_queries_author_id', 'queries', type_='foreignkey')
    op.drop_column('queries', 'author_id')

    op.drop_index(op.f('ix_projects_team_id'), table_name='projects')
    op.drop_constraint('fk_projects_team_id', 'projects', type_='foreignkey')
    op.drop_column('projects', 'team_id')

    op.drop_constraint('fk_subscriptions_team_id', 'subscriptions', type_='foreignkey')
    op.drop_column('subscriptions', 'team_id')

    op.drop_index('ix_team_invitations_token_hash', table_name='team_invitations')
    op.drop_index(op.f('ix_team_invitations_team_id'), table_name='team_invitations')
    op.drop_table('team_invitations')

    op.drop_index(op.f('ix_team_memberships_user_id'), table_name='team_memberships')
    op.drop_index(op.f('ix_team_memberships_team_id'), table_name='team_memberships')
    op.drop_table('team_memberships')

    op.drop_table('teams')
