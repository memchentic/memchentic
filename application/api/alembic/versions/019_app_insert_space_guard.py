"""Make the app_insert_space permission guard fail closed without a user context.

Revision ID: 019
Revises: 018
Create Date: 2026-11-09
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op
from memchentic.settings import get_settings

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None

_SIGNATURE = "app_insert_space(uuid, space_kind, text, uuid)"
_MEMBER_CHECK = "app_is_space_member(uuid)"

# Same body as the newest earlier definition; only the guard expression differs.
_FUNCTION = """
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
  IF {guard} THEN
    RAISE EXCEPTION 'not allowed to create space' USING ERRCODE = '42501';
  END IF;
  INSERT INTO spaces (id, kind, name, created_by_user_id)
  VALUES (p_id, p_kind, p_name, p_created_by);
  {member_insert}
  ON CONFLICT (space_id, user_id) DO NOTHING;
  RETURN p_id;
END;
$$;
"""

# NULL-safe. With no user context the comparison yields NULL; "NULL IS NOT TRUE"
# is true, so the function raises instead of inserting.
_GUARD_FAIL_CLOSED = "(app_is_admin() OR p_created_by = app_user_id()) IS NOT TRUE"
# Guard of the previous definitions (migrations 013 and 018): NULL skips the RAISE.
_GUARD_PREVIOUS = "NOT (app_is_admin() OR p_created_by = app_user_id())"

# Migration 013: membership rows have no acceptance state.
_MEMBER_INSERT_PLAIN = """INSERT INTO space_members (space_id, user_id)
  VALUES (p_id, p_created_by)"""
# Migration 018 (SEC-001): the creator is an accepted member from the start.
_MEMBER_INSERT_ACCEPTED = """INSERT INTO space_members (space_id, user_id, accepted_at)
  VALUES (p_id, p_created_by, now())"""


def _member_insert() -> str:
    """Pick the member insert that matches the schema this migration runs on.

    Returns:
        The 018 form when ``space_members.accepted_at`` exists, else the 013 form.
    """
    has_accepted_at = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'space_members' "
                "AND column_name = 'accepted_at'"
            )
        )
        .scalar()
    )
    return _MEMBER_INSERT_ACCEPTED if has_accepted_at else _MEMBER_INSERT_PLAIN


def upgrade() -> None:
    app_user = (get_settings().postgres_app_user or "memchentic_app").replace("'", "''")
    op.execute(_FUNCTION.format(guard=_GUARD_FAIL_CLOSED, member_insert=_member_insert()))
    # CREATE OR REPLACE keeps the existing privileges. Restate them so the
    # function is never executable by PUBLIC, whatever happened before.
    op.execute(f"REVOKE ALL ON FUNCTION {_SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {_SIGNATURE} TO {app_user}")
    # OQ-028: app_is_space_member has been executable by PUBLIC since migration
    # 001. Only the app role evaluates the policies that call it.
    op.execute(f"REVOKE ALL ON FUNCTION {_MEMBER_CHECK} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {_MEMBER_CHECK} TO {app_user}")


def downgrade() -> None:
    op.execute(f"GRANT EXECUTE ON FUNCTION {_MEMBER_CHECK} TO PUBLIC")
    op.execute(_FUNCTION.format(guard=_GUARD_PREVIOUS, member_insert=_member_insert()))
