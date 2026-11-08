"""Create memchentic_app role and fix space_members RLS write policy.

Revision ID: 011
Revises: 010
Create Date: 2026-11-08
"""

from typing import Sequence, Union

from alembic import op

from memchentic.settings import get_settings

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = (
    "users",
    "user_identities",
    "spaces",
    "space_members",
    "memories",
    "api_keys",
    "oauth_clients",
    "auth_codes",
    "auth_refresh_tokens",
    "gui_sessions",
    "internal_signing_keys",
    "auth_groups",
    "auth_group_members",
    "instance_settings",
)


def upgrade() -> None:
    settings = get_settings()
    app_user = (settings.postgres_app_user or "memchentic_app").replace("'", "''")
    app_password = (settings.postgres_app_password or "").replace("'", "''")
    # Without a configured password the runtime stays on the owner URL, so the
    # role must not be able to log in at all.
    login = f"LOGIN PASSWORD '{app_password}'" if app_password else "NOLOGIN PASSWORD NULL"

    op.execute(
        f"""
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{app_user}') THEN
            CREATE ROLE {app_user} {login}
              NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
          ELSE
            ALTER ROLE {app_user} WITH {login}
              NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
          END IF;
        END
        $$;
        """
    )
    db_name = settings.postgres_db.replace('"', "")
    op.execute(f'GRANT CONNECT ON DATABASE "{db_name}" TO {app_user}')
    op.execute(f"GRANT USAGE ON SCHEMA public TO {app_user}")
    for table in _TABLES:
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO {app_user}"
        )
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {app_user}")
    op.execute(
        f"""
        ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER IN SCHEMA public
        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {app_user}
        """
    )
    # Test DB may differ from settings.postgres_db; also grant on the connected DB.
    op.execute(
        f"""
        DO $$
        DECLARE
          db text := current_database();
        BEGIN
          EXECUTE format('GRANT CONNECT ON DATABASE %I TO {app_user}', db);
        END
        $$;
        """
    )
    op.execute("DROP POLICY IF EXISTS space_members_write ON space_members")
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


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS space_members_write ON space_members")
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
