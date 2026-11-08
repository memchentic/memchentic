"""auth group IdP binds and member source

Revision ID: 008
Revises: 007
Create Date: 2026-11-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "auth_groups",
        sa.Column("idp_group_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "auth_groups",
        sa.Column("idp_group_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "auth_group_members",
        sa.Column(
            "source",
            sa.String(length=16),
            nullable=False,
            server_default="manual",
        ),
    )


def downgrade() -> None:
    op.drop_column("auth_group_members", "source")
    op.drop_column("auth_groups", "idp_group_name")
    op.drop_column("auth_groups", "idp_group_id")
