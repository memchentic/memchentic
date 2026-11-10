"""Normalise stored memory tags: trim, lower-case, drop empty, de-duplicate.

Revision ID: 022
Revises: 017
Create Date: 2026-11-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "022"
down_revision = "017"
branch_labels = None
depends_on = None


def _normalise(tags: list[str]) -> list[str]:
    """Copy of ``memchentic.services.validation.normalise_tags`` at this revision.

    Migrations do not import application code that can change later.
    """
    seen: dict[str, None] = {}
    for tag in tags:
        cleaned = tag.strip().lower()
        if cleaned:
            seen.setdefault(cleaned, None)
    return list(seen)


def upgrade() -> None:
    conn = op.get_bind()
    # memories has FORCE ROW LEVEL SECURITY. Make every row visible to this
    # transaction in case the owner role is not a superuser.
    conn.execute(sa.text("SELECT set_config('app.is_admin', 'true', true)"))
    rows = conn.execute(sa.text("SELECT id, tags FROM memories WHERE cardinality(tags) > 0")).all()
    update = sa.text("UPDATE memories SET tags = :tags WHERE id = :id").bindparams(
        sa.bindparam("tags", type_=postgresql.ARRAY(sa.String()))
    )
    for row_id, tags in rows:
        cleaned = _normalise(list(tags))
        if cleaned != list(tags):
            conn.execute(update, {"tags": cleaned, "id": row_id})
    conn.execute(sa.text("SELECT set_config('app.is_admin', 'false', true)"))


def downgrade() -> None:
    # The original spelling of each tag is not kept. Nothing to restore.
    pass
