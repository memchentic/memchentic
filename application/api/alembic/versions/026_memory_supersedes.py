"""Add memories.supersedes_id for declared conflict links.

Revision ID: 026
Revises: 025
Create Date: 2026-11-22
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memories",
        sa.Column(
            "supersedes_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("memories.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_memories_supersedes_id", "memories", ["supersedes_id"])


def downgrade() -> None:
    op.drop_index("ix_memories_supersedes_id", table_name="memories")
    op.drop_column("memories", "supersedes_id")
