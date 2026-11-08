"""SECURITY DEFINER helper to resolve API keys before RLS context is set.

Revision ID: 015
Revises: 014
Create Date: 2026-11-08
"""

from __future__ import annotations

from alembic import op

from memchentic.settings import get_settings

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    app_user = (get_settings().postgres_app_user or "memchentic_app").replace("'", "''")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_resolve_api_key(p_hash text)
        RETURNS TABLE (
          user_id uuid,
          email text,
          display_name text,
          provider text
        )
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public
        AS $$
          SELECT u.id, u.email::text, u.display_name::text, u.provider::text
          FROM api_keys k
          JOIN users u ON u.id = k.user_id
          WHERE k.key_hash = p_hash AND k.revoked_at IS NULL
          LIMIT 1
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION app_resolve_api_key(text) FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION app_resolve_api_key(text) TO {app_user}")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS app_resolve_api_key(text)")
