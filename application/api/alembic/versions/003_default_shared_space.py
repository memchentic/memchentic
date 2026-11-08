"""per-user default shared group for visibility=shared writes

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
    # FORCE RLS on spaces + policies that touch space_members can recurse when
    # PostgreSQL validates a new FK onto spaces (seen on PG 18 / CNPG).
    op.execute("ALTER TABLE spaces NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE spaces DISABLE ROW LEVEL SECURITY")
    try:
        op.add_column(
            "users",
            sa.Column(
                "default_shared_space_id", postgresql.UUID(as_uuid=True), nullable=True
            ),
        )
        op.create_foreign_key(
            "fk_users_default_shared_space",
            "users",
            "spaces",
            ["default_shared_space_id"],
            ["id"],
            ondelete="SET NULL",
        )
    finally:
        op.execute("ALTER TABLE spaces ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE spaces FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE spaces NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE spaces DISABLE ROW LEVEL SECURITY")
    try:
        op.drop_constraint("fk_users_default_shared_space", "users", type_="foreignkey")
        op.drop_column("users", "default_shared_space_id")
    finally:
        op.execute("ALTER TABLE spaces ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE spaces FORCE ROW LEVEL SECURITY")
