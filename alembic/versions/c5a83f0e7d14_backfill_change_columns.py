"""name the column on changes recorded before constraints carried one

Revision ID: c5a83f0e7d14
Revises: b2e9d47a1c06
Create Date: 2026-10-26 10:40:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c5a83f0e7d14'
down_revision: Union[str, Sequence[str], None] = 'b2e9d47a1c06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # A unique constraint over one column belongs to that column.
    op.execute(
        """
        UPDATE schema_changes
        SET column_name = TRIM(BOTH ')' FROM TRIM(BOTH '(' FROM detail))
        WHERE column_name IS NULL
          AND kind IN ('unique_added', 'unique_removed')
          AND detail LIKE '(%)'
          AND POSITION(',' IN detail) = 0
        """
    )

    # So does a foreign key over one column.
    op.execute(
        """
        UPDATE schema_changes
        SET column_name = SPLIT_PART(detail, ' -> ', 1)
        WHERE column_name IS NULL
          AND kind IN ('foreign_key_added', 'foreign_key_removed', 'foreign_key_changed')
          AND POSITION(' -> ' IN detail) > 0
          AND POSITION(',' IN SPLIT_PART(detail, ' -> ', 1)) = 0
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        """
        UPDATE schema_changes
        SET column_name = NULL
        WHERE kind IN ('unique_added', 'unique_removed',
                       'foreign_key_added', 'foreign_key_removed', 'foreign_key_changed')
        """
    )
