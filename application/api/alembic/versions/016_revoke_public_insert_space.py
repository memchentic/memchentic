"""Revoke PUBLIC execute on app_insert_space SECURITY DEFINER helper.

Revision ID: 016
Revises: 015
Create Date: 2026-11-08
"""

from __future__ import annotations

from alembic import op

from memchentic.settings import get_settings

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    app_user = (get_settings().postgres_app_user or "memchentic_app").replace("'", "''")
    op.execute(
        "REVOKE ALL ON FUNCTION app_insert_space(uuid, space_kind, text, uuid) FROM PUBLIC"
    )
    op.execute(
        f"GRANT EXECUTE ON FUNCTION app_insert_space(uuid, space_kind, text, uuid) TO {app_user}"
    )


def downgrade() -> None:
    op.execute(
        "GRANT EXECUTE ON FUNCTION app_insert_space(uuid, space_kind, text, uuid) TO PUBLIC"
    )
