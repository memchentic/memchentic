"""per-user default shared team for visibility=shared writes

Revision ID: 003
Revises: 002
Create Date: 2026-11-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("default_shared_space_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_users_default_shared_space",
        "users",
        "spaces",
        ["default_shared_space_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_default_shared_space", "users", type_="foreignkey")
    op.drop_column("users", "default_shared_space_id")
