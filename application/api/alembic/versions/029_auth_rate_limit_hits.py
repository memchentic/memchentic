"""Shared auth rate-limit hits for multi-replica coordination.

Revision ID: 029
Revises: 028
Create Date: 2026-11-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from memchentic.settings import get_settings

revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    app_user = (get_settings().postgres_app_user or "memchentic_app").replace("'", "''")

    op.create_table(
        "auth_rate_limit_hits",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("bucket", sa.Text(), nullable=False),
        sa.Column(
            "hit_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_auth_rate_limit_hits_bucket_hit_at",
        "auth_rate_limit_hits",
        ["bucket", "hit_at"],
    )
    # Global infra table: no RLS. Every app replica must count the same hits.
    op.execute(f"GRANT SELECT, INSERT, DELETE ON TABLE auth_rate_limit_hits TO {app_user}")
    op.execute(
        f"GRANT USAGE, SELECT ON SEQUENCE auth_rate_limit_hits_id_seq TO {app_user}"
    )


def downgrade() -> None:
    app_user = (get_settings().postgres_app_user or "memchentic_app").replace("'", "''")
    op.execute(
        f"REVOKE USAGE, SELECT ON SEQUENCE auth_rate_limit_hits_id_seq FROM {app_user}"
    )
    op.execute(f"REVOKE SELECT, INSERT, DELETE ON TABLE auth_rate_limit_hits FROM {app_user}")
    op.drop_index("ix_auth_rate_limit_hits_bucket_hit_at", table_name="auth_rate_limit_hits")
    op.drop_table("auth_rate_limit_hits")
