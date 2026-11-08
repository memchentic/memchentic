"""Grant RLS helpers to the app role; relax personal-space bootstrap insert.

Revision ID: 013
Revises: 012
Create Date: 2026-11-08
"""

from typing import Sequence, Union

from alembic import op

from memchentic.settings import get_settings

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    settings = get_settings()
    app_user = (settings.postgres_app_user or "memchentic_app").replace("'", "''")
    op.execute(f"GRANT EXECUTE ON FUNCTION app_user_id() TO {app_user}")
    op.execute(f"GRANT EXECUTE ON FUNCTION app_is_admin() TO {app_user}")
    op.execute(f"GRANT EXECUTE ON FUNCTION app_is_space_member(uuid) TO {app_user}")

    # SECURITY DEFINER helper so INSERT ... RETURNING can see the new row under
    # FORCE RLS without relying on SELECT policies during the same statement.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_insert_space(
          p_id uuid,
          p_kind space_kind,
          p_name text,
          p_created_by uuid
        ) RETURNS uuid
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public
        AS $$
        BEGIN
          IF NOT (
            app_is_admin()
            OR p_created_by = app_user_id()
          ) THEN
            RAISE EXCEPTION 'not allowed to create space' USING ERRCODE = '42501';
          END IF;
          INSERT INTO spaces (id, kind, name, created_by_user_id)
          VALUES (p_id, p_kind, p_name, p_created_by);
          INSERT INTO space_members (space_id, user_id)
          VALUES (p_id, p_created_by)
          ON CONFLICT (space_id, user_id) DO NOTHING;
          RETURN p_id;
        END;
        $$;
        """
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION app_insert_space(uuid, space_kind, text, uuid) TO {app_user}")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS app_insert_space(uuid, space_kind, text, uuid)")
