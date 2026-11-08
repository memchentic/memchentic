"""auth_codes and auth_refresh_tokens user_id NOT NULL

Revision ID: 010
Revises: 009
Create Date: 2026-11-08
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM auth_codes WHERE user_id IS NULL")
    op.execute("DELETE FROM auth_refresh_tokens WHERE user_id IS NULL")
    op.alter_column(
        "auth_codes",
        "user_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.alter_column(
        "auth_refresh_tokens",
        "user_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "auth_refresh_tokens",
        "user_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.alter_column(
        "auth_codes",
        "user_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
