"""Group invites (space_members.accepted_at) and edit attribution column.

Revision ID: 018
Revises: 022
Create Date: 2026-11-09
"""

# Follows 022: revision numbers were reserved per work package, not assigned in chain order.

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from memchentic.settings import get_settings

revision = "018"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    app_user = (get_settings().postgres_app_user or "memchentic_app").replace("'", "''")

    # 1. Consent column. NULL = invited, not accepted. No server default, so a
    #    row inserted without the column is pending (fail closed).
    op.add_column(
        "space_members",
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Memberships that exist today predate invites: keep them working.
    # space_members has FORCE RLS; bind the admin setting for this transaction
    # so the backfill also works when the owner role is not a superuser.
    op.execute("SELECT set_config('app.is_admin', 'true', true)")
    op.execute("UPDATE space_members SET accepted_at = COALESCE(created_at, now())")
    pending = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM space_members WHERE accepted_at IS NULL"))
        .scalar()
    )
    op.execute("SELECT set_config('app.is_admin', 'false', true)")
    if pending:
        raise RuntimeError(
            f"018: {pending} space_members rows were not backfilled; "
            "refusing to continue because those users would lose access"
        )

    # 2. RLS membership test: only accepted members count.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_is_space_member(sid uuid) RETURNS boolean AS $$
          SELECT app_is_admin() OR EXISTS (
            SELECT 1 FROM space_members sm
            WHERE sm.space_id = sid
              AND sm.user_id = app_user_id()
              AND sm.accepted_at IS NOT NULL
          )
        $$ LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public
        """
    )

    # 3. The creator's own membership is accepted at creation. The guard is
    #    copied from 013 unchanged (SEC-021 hardens it in a later migration).
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
          INSERT INTO space_members (space_id, user_id, accepted_at)
          VALUES (p_id, p_created_by, now())
          ON CONFLICT (space_id, user_id) DO NOTHING;
          RETURN p_id;
        END;
        $$;
        """
    )

    # 4. A group creator may insert only pending rows for other users.
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

    # 5. Accept = the invited user updates their own row. The app role may
    #    update only the accepted_at column, so a user cannot move their row
    #    to another space. Group creators can no longer update other rows.
    op.execute("DROP POLICY IF EXISTS space_members_update ON space_members")
    op.execute(
        """
        CREATE POLICY space_members_update ON space_members FOR UPDATE USING (
          app_is_admin() OR user_id = app_user_id()
        ) WITH CHECK (
          app_is_admin() OR user_id = app_user_id()
        )
        """
    )
    op.execute(f"REVOKE UPDATE ON TABLE space_members FROM {app_user}")
    op.execute(f"GRANT UPDATE (accepted_at) ON TABLE space_members TO {app_user}")

    # 6. Edit attribution (SEC-011). NULL until the first edit.
    op.add_column(
        "memories",
        sa.Column(
            "updated_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    app_user = (get_settings().postgres_app_user or "memchentic_app").replace("'", "''")

    op.drop_column("memories", "updated_by_user_id")

    op.execute(f"REVOKE UPDATE (accepted_at) ON TABLE space_members FROM {app_user}")
    op.execute(f"GRANT UPDATE ON TABLE space_members TO {app_user}")
    op.execute("DROP POLICY IF EXISTS space_members_update ON space_members")
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
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_is_space_member(sid uuid) RETURNS boolean AS $$
          SELECT app_is_admin() OR EXISTS (
            SELECT 1 FROM space_members sm
            WHERE sm.space_id = sid AND sm.user_id = app_user_id()
          )
        $$ LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = public
        """
    )
    # Pending invites cannot be represented below 018; dropping the column
    # turns every pending row into a full membership.
    op.drop_column("space_members", "accepted_at")
