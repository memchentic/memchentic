"""Add memory_recalls event log for retrieval metrics.

Revision ID: 027
Revises: 026
Create Date: 2026-11-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from memchentic.settings import get_settings

revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    app_user = (get_settings().postgres_app_user or "memchentic_app").replace("'", "''")

    op.create_table(
        "memory_recalls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "memory_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "space_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "recalled_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "recalled_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source", sa.String(32), nullable=False),
    )
    op.create_index(
        "ix_memory_recalls_space_recalled_at",
        "memory_recalls",
        ["space_id", "recalled_at"],
    )
    op.create_index(
        "ix_memory_recalls_memory_recalled_at",
        "memory_recalls",
        ["memory_id", "recalled_at"],
    )
    op.create_index("ix_memory_recalls_recalled_at", "memory_recalls", ["recalled_at"])

    op.execute("ALTER TABLE memory_recalls ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE memory_recalls FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY memory_recalls_all ON memory_recalls FOR ALL USING (
          app_is_space_member(space_id)
        ) WITH CHECK (
          app_is_space_member(space_id)
        )
        """
    )
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE memory_recalls TO {app_user}"
    )


def downgrade() -> None:
    app_user = (get_settings().postgres_app_user or "memchentic_app").replace("'", "''")
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLE memory_recalls FROM {app_user}")
    op.execute("DROP POLICY IF EXISTS memory_recalls_all ON memory_recalls")
    op.execute("ALTER TABLE memory_recalls NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE memory_recalls DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_memory_recalls_recalled_at", table_name="memory_recalls")
    op.drop_index("ix_memory_recalls_memory_recalled_at", table_name="memory_recalls")
    op.drop_index("ix_memory_recalls_space_recalled_at", table_name="memory_recalls")
    op.drop_table("memory_recalls")
