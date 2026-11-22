"""Add space_members.role (user|admin) and roster-manager RLS helpers.

Revision ID: 023
Revises: 020
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from memchentic.settings import get_settings

revision = "023"
down_revision = "020"
branch_labels = None
depends_on = None

_INSERT_SPACE = "app_insert_space(uuid, space_kind, text, uuid)"
_ROSTER_MANAGER = "app_is_space_roster_manager(uuid)"
_SET_ROLE = "app_set_space_member_role(uuid, uuid, space_member_role)"

_APP_INSERT_SPACE = """
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
  IF (app_is_admin() OR p_created_by = app_user_id()) IS NOT TRUE THEN
    RAISE EXCEPTION 'not allowed to create space' USING ERRCODE = '42501';
  END IF;
  INSERT INTO spaces (id, kind, name, created_by_user_id)
  VALUES (p_id, p_kind, p_name, p_created_by);
  INSERT INTO space_members (space_id, user_id, accepted_at, role)
  VALUES (p_id, p_created_by, now(), 'admin')
  ON CONFLICT (space_id, user_id) DO NOTHING;
  RETURN p_id;
END;
$$;
"""

_APP_INSERT_SPACE_PREV = """
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
  IF (app_is_admin() OR p_created_by = app_user_id()) IS NOT TRUE THEN
    RAISE EXCEPTION 'not allowed to create space' USING ERRCODE = '42501';
  END IF;
  INSERT INTO spaces (id, kind, name, created_by_user_id)
  VALUES (p_id, p_kind, p_name, p_created_by);
  INSERT INTO space_members (space_id, user_id, accepted_at)
  VALUES (p_id, p_created_by, now())
  ON CONFLICT (space_id, user_id) DO NOTHING;
  RETURN p_id;
END;
$$;
"""


def upgrade() -> None:
    app_user = (get_settings().postgres_app_user or "memchentic_app").replace("'", "''")

    op.execute(
        "CREATE TYPE space_member_role AS ENUM ('user', 'admin')"
    )
    op.add_column(
        "space_members",
        sa.Column(
            "role",
            sa.Enum("user", "admin", name="space_member_role", create_type=False),
            nullable=False,
            server_default="user",
        ),
    )

    # Creator memberships are group admins; everyone else stays user.
    op.execute(
        """
        UPDATE space_members sm
        SET role = 'admin'
        FROM spaces s
        WHERE sm.space_id = s.id
          AND s.created_by_user_id = sm.user_id
        """
    )

    op.execute(_APP_INSERT_SPACE)
    op.execute(f"REVOKE ALL ON FUNCTION {_INSERT_SPACE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {_INSERT_SPACE} TO {app_user}")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_is_space_roster_manager(sid uuid)
        RETURNS boolean AS $$
          SELECT app_is_admin()
            OR EXISTS (
              SELECT 1 FROM spaces s
              WHERE s.id = sid AND s.created_by_user_id = app_user_id()
            )
            OR EXISTS (
              SELECT 1 FROM space_members sm
              WHERE sm.space_id = sid
                AND sm.user_id = app_user_id()
                AND sm.accepted_at IS NOT NULL
                AND sm.role = 'admin'
            )
        $$ LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION {_ROSTER_MANAGER} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {_ROSTER_MANAGER} TO {app_user}")

    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_set_space_member_role(
          p_space uuid,
          p_user uuid,
          p_role space_member_role
        ) RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public
        AS $$
        BEGIN
          IF app_is_admin() IS NOT TRUE AND NOT EXISTS (
            SELECT 1 FROM spaces s
            WHERE s.id = p_space AND s.created_by_user_id = app_user_id()
          ) THEN
            RAISE EXCEPTION 'not allowed to change member role' USING ERRCODE = '42501';
          END IF;
          UPDATE space_members
          SET role = p_role
          WHERE space_id = p_space AND user_id = p_user;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'member not found' USING ERRCODE = 'P0002';
          END IF;
        END;
        $$;
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION {_SET_ROLE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {_SET_ROLE} TO {app_user}")

    op.execute("DROP POLICY IF EXISTS space_members_insert ON space_members")
    op.execute(
        """
        CREATE POLICY space_members_insert ON space_members FOR INSERT WITH CHECK (
          app_is_admin() OR (
            (accepted_at IS NULL OR user_id = app_user_id())
            AND app_is_space_roster_manager(space_id)
          )
        )
        """
    )

    op.execute("DROP POLICY IF EXISTS space_members_delete ON space_members")
    op.execute(
        """
        CREATE POLICY space_members_delete ON space_members FOR DELETE USING (
          app_is_admin()
          OR user_id = app_user_id()
          OR app_is_space_roster_manager(space_id)
        )
        """
    )


def downgrade() -> None:
    app_user = (get_settings().postgres_app_user or "memchentic_app").replace("'", "''")

    op.execute("DROP POLICY IF EXISTS space_members_delete ON space_members")
    op.execute(
        """
        CREATE POLICY space_members_delete ON space_members FOR DELETE USING (
          app_is_admin()
          OR user_id = app_user_id()
          OR EXISTS (
            SELECT 1 FROM spaces s
            WHERE s.id = space_members.space_id AND s.created_by_user_id = app_user_id()
          )
        )
        """
    )

    op.execute("DROP POLICY IF EXISTS space_members_insert ON space_members")
    op.execute(
        """
        CREATE POLICY space_members_insert ON space_members FOR INSERT WITH CHECK (
          app_is_admin() OR (
            (accepted_at IS NULL OR user_id = app_user_id())
            AND EXISTS (
              SELECT 1 FROM spaces s
              WHERE s.id = space_members.space_id AND s.created_by_user_id = app_user_id()
            )
          )
        )
        """
    )

    op.execute(f"DROP FUNCTION IF EXISTS {_SET_ROLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {_ROSTER_MANAGER}")

    op.execute(_APP_INSERT_SPACE_PREV)
    op.execute(f"REVOKE ALL ON FUNCTION {_INSERT_SPACE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {_INSERT_SPACE} TO {app_user}")

    op.drop_column("space_members", "role")
    op.execute("DROP TYPE IF EXISTS space_member_role")
