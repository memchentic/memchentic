"""initial schema with pgvector, FTS, RLS

Revision ID: 001
Revises:
Create Date: 2026-11-07
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    space_kind = postgresql.ENUM("personal", "team", name="space_kind", create_type=False)
    memory_kind = postgresql.ENUM("entry", "guide", name="memory_kind", create_type=False)
    entry_type = postgresql.ENUM(
        "fact",
        "preference",
        "decision",
        "instruction",
        "feedback",
        "insight",
        "note",
        name="entry_type",
        create_type=False,
    )
    op.execute("CREATE TYPE space_kind AS ENUM ('personal', 'team')")
    op.execute("CREATE TYPE memory_kind AS ENUM ('entry', 'guide')")
    op.execute(
        "CREATE TYPE entry_type AS ENUM "
        "('fact', 'preference', 'decision', 'instruction', 'feedback', 'insight', 'note')"
    )

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_subject", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("provider", "provider_subject", name="uq_users_provider_sub"),
    )

    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("key_prefix", sa.String(16), nullable=False),
        sa.Column("key_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "spaces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", space_kind, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_spaces_personal_owner
        ON spaces (created_by_user_id)
        WHERE kind = 'personal'
        """
    )

    op.create_table(
        "space_members",
        sa.Column("space_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("spaces.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "memories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("spaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", memory_kind, nullable=False),
        sa.Column("entry_type", entry_type, nullable=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("tags", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("path_hint", sa.String(1024), nullable=True),
        sa.Column("repo_hint", sa.String(512), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("embedding", Vector(384), nullable=True),
        sa.Column("search_tsv", postgresql.TSVECTOR(), nullable=True),
        sa.Column("provenance", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_memories_space_updated", "memories", ["space_id", "updated_at"])
    op.execute("CREATE INDEX ix_memories_tags ON memories USING gin (tags)")
    op.execute("CREATE INDEX ix_memories_search_tsv ON memories USING gin (search_tsv)")
    op.execute(
        "CREATE INDEX ix_memories_embedding ON memories USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION memories_search_tsv_trigger() RETURNS trigger AS $$
        BEGIN
          NEW.search_tsv :=
            setweight(to_tsvector('english', coalesce(NEW.title, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(NEW.body, '')), 'B');
          RETURN NEW;
        END
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER memories_tsv_update
        BEFORE INSERT OR UPDATE OF title, body ON memories
        FOR EACH ROW EXECUTE FUNCTION memories_search_tsv_trigger()
        """
    )

    op.create_table(
        "oauth_clients",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("client_id", sa.String(128), nullable=False, unique=True),
        sa.Column("client_secret_hash", sa.String(255), nullable=True),
        sa.Column("redirect_uris", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "auth_codes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(128), nullable=False, unique=True),
        sa.Column("client_id", sa.String(128), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("redirect_uri", sa.String(1024), nullable=False),
        sa.Column("scope", sa.String(512), nullable=False),
        sa.Column("code_challenge", sa.String(128), nullable=True),
        sa.Column("code_challenge_method", sa.String(16), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "auth_refresh_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True),
        sa.Column("client_id", sa.String(128), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("scope", sa.String(512), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "internal_signing_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kid", sa.String(64), nullable=False, unique=True),
        sa.Column("private_pem", sa.Text(), nullable=False),
        sa.Column("public_pem", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
    )

    # RLS helpers
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_is_admin() RETURNS boolean AS $$
          SELECT coalesce(current_setting('app.is_admin', true), 'false') = 'true'
        $$ LANGUAGE sql STABLE
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_user_id() RETURNS uuid AS $$
          SELECT NULLIF(current_setting('app.user_id', true), '')::uuid
        $$ LANGUAGE sql STABLE
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

    for table in ("spaces", "space_members", "memories", "api_keys"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

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
    op.execute(
        """
        CREATE POLICY spaces_insert ON spaces FOR INSERT WITH CHECK (
          app_is_admin() OR created_by_user_id = app_user_id()
        )
        """
    )
    op.execute(
        """
        CREATE POLICY spaces_update ON spaces FOR UPDATE USING (
          app_is_admin() OR created_by_user_id = app_user_id()
        )
        """
    )
    op.execute(
        """
        CREATE POLICY spaces_delete ON spaces FOR DELETE USING (
          app_is_admin() OR created_by_user_id = app_user_id()
        )
        """
    )

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
          ) OR user_id = app_user_id()
        )
        """
    )

    op.execute(
        """
        CREATE POLICY memories_all ON memories FOR ALL USING (
          app_is_space_member(space_id)
        ) WITH CHECK (
          app_is_space_member(space_id)
        )
        """
    )

    op.execute(
        """
        CREATE POLICY api_keys_all ON api_keys FOR ALL USING (
          app_is_admin() OR user_id = app_user_id()
        ) WITH CHECK (
          app_is_admin() OR user_id = app_user_id()
        )
        """
    )


def downgrade() -> None:
    for table in ("api_keys", "memories", "space_members", "spaces"):
        op.execute(f"DROP POLICY IF EXISTS {table}_all ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_select ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_insert ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_update ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_delete ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_write ON {table}")
    op.execute("DROP POLICY IF EXISTS spaces_select ON spaces")
    op.execute("DROP POLICY IF EXISTS spaces_insert ON spaces")
    op.execute("DROP POLICY IF EXISTS spaces_update ON spaces")
    op.execute("DROP POLICY IF EXISTS spaces_delete ON spaces")
    op.execute("DROP POLICY IF EXISTS space_members_select ON space_members")
    op.execute("DROP POLICY IF EXISTS space_members_write ON space_members")
    op.execute("DROP POLICY IF EXISTS memories_all ON memories")
    op.execute("DROP POLICY IF EXISTS api_keys_all ON api_keys")

    op.drop_table("internal_signing_keys")
    op.drop_table("auth_refresh_tokens")
    op.drop_table("auth_codes")
    op.drop_table("oauth_clients")
    op.execute("DROP TRIGGER IF EXISTS memories_tsv_update ON memories")
    op.execute("DROP FUNCTION IF EXISTS memories_search_tsv_trigger")
    op.drop_table("memories")
    op.drop_table("space_members")
    op.drop_table("spaces")
    op.drop_table("api_keys")
    op.drop_table("users")
    op.execute("DROP FUNCTION IF EXISTS app_is_space_member(uuid)")
    op.execute("DROP FUNCTION IF EXISTS app_user_id()")
    op.execute("DROP FUNCTION IF EXISTS app_is_admin()")
    op.execute("DROP TYPE IF EXISTS entry_type")
    op.execute("DROP TYPE IF EXISTS memory_kind")
    op.execute("DROP TYPE IF EXISTS space_kind")
