"""Track recall usage on memories for sliding TTL and usage re-rank.

Revision ID: 017
Revises: 016
Create Date: 2026-11-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memories",
        sa.Column("recall_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "memories",
        sa.Column("last_recalled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("memories", "last_recalled_at")
    op.drop_column("memories", "recall_count")
