"""rename space_kind enum value team → group

Revision ID: 005
Revises: 004
Create Date: 2026-11-07

Existing databases that applied 001 with ``'team'`` need this rename.
Greenfield installs that create ``space_kind`` as ``('personal', 'group')``
from the rewritten 001 skip the rename (label already absent).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_enum e
                JOIN pg_type t ON e.enumtypid = t.oid
                WHERE t.typname = 'space_kind' AND e.enumlabel = 'team'
            ) THEN
                ALTER TYPE space_kind RENAME VALUE 'team' TO 'group';
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM pg_enum e
                JOIN pg_type t ON e.enumtypid = t.oid
                WHERE t.typname = 'space_kind' AND e.enumlabel = 'group'
            ) THEN
                ALTER TYPE space_kind RENAME VALUE 'group' TO 'team';
            END IF;
        END
        $$;
        """
    )
