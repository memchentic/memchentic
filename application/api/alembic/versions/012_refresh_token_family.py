"""Add family_id to auth_refresh_tokens; break spaces/space_members RLS recursion.

Revision ID: 012
Revises: 011
Create Date: 2026-11-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE auth_refresh_tokens
        ADD COLUMN IF NOT EXISTS family_id UUID
        """
    )
    op.execute("UPDATE auth_refresh_tokens SET family_id = id WHERE family_id IS NULL")
    op.alter_column(
        "auth_refresh_tokens",
        "family_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_auth_refresh_tokens_family_id "
        "ON auth_refresh_tokens (family_id)"
    )

    # space_members_write was FOR ALL, so SELECTs also ran its USING clause,
    # which queries spaces while spaces_select queries space_members → recursion
    # under the non-superuser app role. Narrow write to DML and let creators
    # see their own spaces before the first membership row exists.
    op.execute("DROP POLICY IF EXISTS spaces_select ON spaces")
    op.execute(
        """
        CREATE POLICY spaces_select ON spaces FOR SELECT USING (
          app_is_admin()
          OR created_by_user_id = app_user_id()
          OR EXISTS (
            SELECT 1 FROM space_members sm
            WHERE sm.space_id = spaces.id AND sm.user_id = app_user_id()
          )
        )
        """
    )
    # INSERT ... RETURNING also applies SELECT policies; allow a caller to see
    # their own membership row so the first personal-space join can land.
    op.execute("DROP POLICY IF EXISTS space_members_select ON space_members")
    op.execute(
        """
        CREATE POLICY space_members_select ON space_members FOR SELECT USING (
          app_is_admin()
          OR user_id = app_user_id()
          OR app_is_space_member(space_id)
        )
        """
    )
    op.execute("DROP POLICY IF EXISTS space_members_write ON space_members")
    op.execute(
        """
        CREATE POLICY space_members_insert ON space_members FOR INSERT WITH CHECK (
          app_is_admin() OR EXISTS (
            SELECT 1 FROM spaces s
            WHERE s.id = space_members.space_id AND s.created_by_user_id = app_user_id()
          ) OR user_id = app_user_id()
        )
        """
    )
    op.execute(
        """
        CREATE POLICY space_members_update ON space_members FOR UPDATE USING (
          app_is_admin() OR EXISTS (
            SELECT 1 FROM spaces s
            WHERE s.id = space_members.space_id AND s.created_by_user_id = app_user_id()
          )
        ) WITH CHECK (
          app_is_admin() OR EXISTS (
            SELECT 1 FROM spaces s
            WHERE s.id = space_members.space_id AND s.created_by_user_id = app_user_id()
          )
        )
        """
    )
    op.execute(
        """
        CREATE POLICY space_members_delete ON space_members FOR DELETE USING (
          app_is_admin() OR EXISTS (
            SELECT 1 FROM spaces s
            WHERE s.id = space_members.space_id AND s.created_by_user_id = app_user_id()
          )
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS space_members_delete ON space_members")
    op.execute("DROP POLICY IF EXISTS space_members_update ON space_members")
    op.execute("DROP POLICY IF EXISTS space_members_insert ON space_members")
    op.execute("DROP POLICY IF EXISTS space_members_write ON space_members")
    op.execute("DROP POLICY IF EXISTS space_members_select ON space_members")
    op.execute(
        """
        CREATE POLICY space_members_select ON space_members FOR SELECT USING (
          app_is_space_member(space_id)
        )
        """
    )
    op.execute(
        """
        CREATE POLICY space_members_write ON space_members FOR ALL USING (
          app_is_admin() OR EXISTS (
            SELECT 1 FROM spaces s
            WHERE s.id = space_members.space_id AND s.created_by_user_id = app_user_id()
          )
        ) WITH CHECK (
          app_is_admin() OR EXISTS (
            SELECT 1 FROM spaces s
            WHERE s.id = space_members.space_id AND s.created_by_user_id = app_user_id()
          )
        )
        """
    )
    op.execute("DROP POLICY IF EXISTS spaces_select ON spaces")
    op.execute(
        """
        CREATE POLICY spaces_select ON spaces FOR SELECT USING (
          app_is_admin() OR EXISTS (
            SELECT 1 FROM space_members sm
            WHERE sm.space_id = spaces.id AND sm.user_id = app_user_id()
          )
        )
        """
    )
    op.drop_index("ix_auth_refresh_tokens_family_id", table_name="auth_refresh_tokens")
    op.drop_column("auth_refresh_tokens", "family_id")
