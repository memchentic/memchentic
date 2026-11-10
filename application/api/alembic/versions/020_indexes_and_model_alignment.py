"""Add lookup indexes and align column nullability with the ORM models.

Revision ID: 020
Revises: 019
Create Date: 2026-11-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None

# (table, column, backfill expression). Order matters: a column that is
# backfilled from created_at comes after created_at of the same table.
_NOT_NULL_TIMESTAMPS = (
    ("api_keys", "created_at", "now()"),
    ("auth_codes", "created_at", "now()"),
    ("auth_group_members", "created_at", "now()"),
    ("auth_groups", "created_at", "now()"),
    ("auth_refresh_tokens", "created_at", "now()"),
    ("gui_sessions", "created_at", "now()"),
    ("gui_sessions", "last_used_at", "created_at"),
    ("instance_settings", "updated_at", "now()"),
    ("internal_signing_keys", "created_at", "now()"),
    ("memories", "created_at", "now()"),
    ("memories", "updated_at", "created_at"),
    ("oauth_clients", "created_at", "now()"),
    ("space_members", "created_at", "now()"),
    ("spaces", "created_at", "now()"),
    ("user_identities", "created_at", "now()"),
    ("users", "created_at", "now()"),
)


def upgrade() -> None:
    # spaces, space_members, memories and api_keys use FORCE ROW LEVEL
    # SECURITY, which also filters an owner role that is not a superuser.
    # Without the admin flag the DELETE and UPDATE statements below would
    # skip hidden rows. The flag is local to this transaction.
    op.execute("SELECT set_config('app.is_admin', 'true', true)")

    # Lookup indexes (PERF-001). Plain CREATE INDEX: env.py runs all
    # revisions in one transaction, where a concurrent build is not allowed.
    op.create_index("ix_space_members_user_id", "space_members", ["user_id"])
    op.execute(
        "CREATE INDEX ix_memories_space_lower_title ON memories (space_id, lower(btrim(title)))"
    )
    op.create_index("ix_memories_created_by_user_id", "memories", ["created_by_user_id"])

    # A key without a user cannot authenticate (key resolution joins
    # users). Same rule as migration 010 for auth_codes / refresh tokens.
    op.execute("DELETE FROM api_keys WHERE user_id IS NULL")
    op.alter_column(
        "api_keys",
        "user_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )

    # Timestamps: every column has DEFAULT now(), so NULL rows are not
    # expected. Backfill defensively; SET NOT NULL fails loudly if a NULL
    # is left.
    for table, column, backfill in _NOT_NULL_TIMESTAMPS:
        op.execute(f"UPDATE {table} SET {column} = {backfill} WHERE {column} IS NULL")
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )

    op.execute("SELECT set_config('app.is_admin', 'false', true)")


def downgrade() -> None:
    # Deleted api_keys rows and backfilled timestamps are not restored.
    for table, column, _backfill in reversed(_NOT_NULL_TIMESTAMPS):
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )
    op.alter_column(
        "api_keys",
        "user_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.drop_index("ix_memories_created_by_user_id", table_name="memories")
    op.execute("DROP INDEX ix_memories_space_lower_title")
    op.drop_index("ix_space_members_user_id", table_name="space_members")
