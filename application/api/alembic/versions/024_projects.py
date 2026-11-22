"""Add projects table and optional memories.project_id.

Revision ID: 024
Revises: 023
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from memchentic.settings import get_settings

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    app_user = (get_settings().postgres_app_user or "memchentic_app").replace("'", "''")

    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "space_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_projects_space_id", "projects", ["space_id"])
    op.create_index(
        "uq_projects_space_lower_name",
        "projects",
        ["space_id", sa.text("lower(btrim(name))")],
        unique=True,
    )

    op.add_column(
        "memories",
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_memories_project_id", "memories", ["project_id"])

    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_check_memory_project_space()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
          proj_space uuid;
        BEGIN
          IF NEW.project_id IS NULL THEN
            RETURN NEW;
          END IF;
          SELECT space_id INTO proj_space FROM projects WHERE id = NEW.project_id;
          IF proj_space IS NULL THEN
            RAISE EXCEPTION 'project not found' USING ERRCODE = '23503';
          END IF;
          IF proj_space IS DISTINCT FROM NEW.space_id THEN
            RAISE EXCEPTION 'project must belong to the memory space' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_memories_project_space
        BEFORE INSERT OR UPDATE OF project_id, space_id ON memories
        FOR EACH ROW EXECUTE FUNCTION app_check_memory_project_space()
        """
    )

    op.execute("ALTER TABLE projects ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE projects FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY projects_all ON projects FOR ALL USING (
          app_is_space_member(space_id)
        ) WITH CHECK (
          app_is_space_member(space_id)
        )
        """
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE projects TO {app_user}")


def downgrade() -> None:
    app_user = (get_settings().postgres_app_user or "memchentic_app").replace("'", "''")
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLE projects FROM {app_user}")
    op.execute("DROP POLICY IF EXISTS projects_all ON projects")
    op.execute("DROP TRIGGER IF EXISTS trg_memories_project_space ON memories")
    op.execute("DROP FUNCTION IF EXISTS app_check_memory_project_space()")
    op.drop_index("ix_memories_project_id", table_name="memories")
    op.drop_column("memories", "project_id")
    op.drop_index("uq_projects_space_lower_name", table_name="projects")
    op.drop_index("ix_projects_space_id", table_name="projects")
    op.drop_table("projects")
