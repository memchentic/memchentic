"""accumulate OAuth provider bindings per email user

Revision ID: 004
Revises: 003
Create Date: 2026-11-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_subject", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("provider", "provider_subject", name="uq_user_identities_provider_sub"),
        sa.UniqueConstraint("user_id", "provider", name="uq_user_identities_user_provider"),
    )
    op.execute(
        """
        INSERT INTO user_identities (id, user_id, provider, provider_subject, created_at)
        SELECT gen_random_uuid(), id, provider, provider_subject, created_at
        FROM users
        """
    )
    op.drop_constraint("uq_users_provider_sub", "users", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint(
        "uq_users_provider_sub", "users", ["provider", "provider_subject"]
    )
    op.drop_table("user_identities")
