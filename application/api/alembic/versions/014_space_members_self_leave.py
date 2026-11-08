"""Allow members to leave groups; block self-join via INSERT.

Revision ID: 014
Revises: 013
Create Date: 2026-11-08
"""

from __future__ import annotations

from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Self-insert let any authenticated user join any space. Personal-space
    # bootstrap uses app_insert_space (SECURITY DEFINER), so INSERT stays
    # creator/admin only.
    op.execute("DROP POLICY IF EXISTS space_members_insert ON space_members")
    op.execute(
        """
        CREATE POLICY space_members_insert ON space_members FOR INSERT WITH CHECK (
          app_is_admin() OR EXISTS (
            SELECT 1 FROM spaces s
            WHERE s.id = space_members.space_id AND s.created_by_user_id = app_user_id()
          )
        )
        """
    )
    # Members must be able to leave themselves; creators/admins can remove others.
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


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS space_members_delete ON space_members")
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
    op.execute("DROP POLICY IF EXISTS space_members_insert ON space_members")
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
