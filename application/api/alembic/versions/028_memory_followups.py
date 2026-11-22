"""Add memory revisions, feedback and event log tables.

Revision ID: 028
Revises: 027
Create Date: 2026-11-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from memchentic.settings import get_settings

revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    app_user = (get_settings().postgres_app_user or "memchentic_app").replace("'", "''")

    op.create_table(
        "memory_revisions",
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
            "revised_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "revised_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column(
            "changed_fields",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index(
        "ix_memory_revisions_memory_revised_at",
        "memory_revisions",
        ["memory_id", "revised_at"],
    )
    op.create_index(
        "ix_memory_revisions_space_revised_at",
        "memory_revisions",
        ["space_id", "revised_at"],
    )

    op.create_table(
        "memory_feedback",
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
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("signal", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("memory_id", "user_id", name="uq_memory_feedback_memory_user"),
    )
    op.create_index(
        "ix_memory_feedback_memory_created_at",
        "memory_feedback",
        ["memory_id", "created_at"],
    )
    op.create_index(
        "ix_memory_feedback_space_created_at",
        "memory_feedback",
        ["space_id", "created_at"],
    )

    op.create_table(
        "memory_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "memory_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "space_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_memory_events_space_created_at",
        "memory_events",
        ["space_id", "created_at"],
    )
    op.create_index("ix_memory_events_created_at", "memory_events", ["created_at"])

    for table in ("memory_revisions", "memory_feedback", "memory_events"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_all ON {table} FOR ALL USING (
              app_is_space_member(space_id)
            ) WITH CHECK (
              app_is_space_member(space_id)
            )
            """
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO {app_user}")


def downgrade() -> None:
    app_user = (get_settings().postgres_app_user or "memchentic_app").replace("'", "''")
    for table in ("memory_events", "memory_feedback", "memory_revisions"):
        op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLE {table} FROM {app_user}")
        op.execute(f"DROP POLICY IF EXISTS {table}_all ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_memory_events_created_at", table_name="memory_events")
    op.drop_index("ix_memory_events_space_created_at", table_name="memory_events")
    op.drop_table("memory_events")

    op.drop_index("ix_memory_feedback_space_created_at", table_name="memory_feedback")
    op.drop_index("ix_memory_feedback_memory_created_at", table_name="memory_feedback")
    op.drop_table("memory_feedback")

    op.drop_index("ix_memory_revisions_space_revised_at", table_name="memory_revisions")
    op.drop_index("ix_memory_revisions_memory_revised_at", table_name="memory_revisions")
    op.drop_table("memory_revisions")