"""app_insert_space must refuse to run without a matching RLS user context."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from memchentic.settings import get_settings
from support import register_user

_CALL = text("SELECT app_insert_space(:id, CAST('group' AS space_kind), :name, :created_by)")
_SET_CONTEXT = text(
    "SELECT set_config('app.user_id', :uid, false), set_config('app.is_admin', :admin, false)"
)
_REFUSED = "not allowed to create space"
_INSERT_SPACE = "app_insert_space(uuid, space_kind, text, uuid)"
_MEMBER_CHECK = "app_is_space_member(uuid)"


def _new_user_id(client: TestClient) -> uuid.UUID:
    return uuid.UUID(register_user(client).user_id)


async def _call(conn: AsyncConnection, space_id: uuid.UUID, created_by: uuid.UUID) -> None:
    await conn.execute(_CALL, {"id": space_id, "name": "guard-test", "created_by": created_by})


def test_app_insert_space_refuses_without_user_context(client: TestClient) -> None:
    target_id = _new_user_id(client)

    async def run() -> None:
        engine = create_async_engine(get_settings().database_url)
        space_id = uuid.uuid4()
        try:
            async with engine.connect() as conn:
                # 1. New connection: app.user_id was never set (NULL).
                with pytest.raises(DBAPIError, match=_REFUSED):
                    await _call(conn, space_id, target_id)
                await conn.rollback()

                # 2. Context cleared the way the pool check-in listener clears it.
                await conn.execute(_SET_CONTEXT, {"uid": "", "admin": "false"})
                with pytest.raises(DBAPIError, match=_REFUSED):
                    await _call(conn, space_id, target_id)
                await conn.rollback()

                # 3. Another user's context is refused.
                await conn.execute(_SET_CONTEXT, {"uid": str(uuid.uuid4()), "admin": "false"})
                with pytest.raises(DBAPIError, match=_REFUSED):
                    await _call(conn, space_id, target_id)
                await conn.rollback()

                # 4. The matching user's context is accepted, and the creator is
                #    an accepted member (migration 018 semantics are kept).
                await conn.execute(_SET_CONTEXT, {"uid": str(target_id), "admin": "false"})
                await _call(conn, space_id, target_id)
                visible = await conn.scalar(
                    text("SELECT count(*) FROM spaces WHERE id = :id"), {"id": space_id}
                )
                assert visible == 1
                accepted = await conn.scalar(
                    text(
                        "SELECT accepted_at IS NOT NULL FROM space_members "
                        "WHERE space_id = :sid AND user_id = :uid"
                    ),
                    {"sid": space_id, "uid": target_id},
                )
                assert accepted is True
                await conn.rollback()  # leave no row behind
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_app_insert_space_admin_context_may_create_for_another_user(client: TestClient) -> None:
    target_id = _new_user_id(client)

    async def run() -> None:
        engine = create_async_engine(get_settings().database_url)
        space_id = uuid.uuid4()
        try:
            async with engine.connect() as conn:
                await conn.execute(_SET_CONTEXT, {"uid": "", "admin": "true"})
                await _call(conn, space_id, target_id)
                owner = await conn.scalar(
                    text("SELECT created_by_user_id FROM spaces WHERE id = :id"),
                    {"id": space_id},
                )
                assert owner == target_id
                await conn.rollback()
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_migration_019_function_definitions_and_privileges() -> None:
    """019 keeps SECURITY DEFINER helpers app-role-only and pending-member aware."""

    async def run() -> None:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with engine.connect() as conn:
                insert_src, insert_secdef, insert_config = (
                    await conn.execute(
                        text(
                            "SELECT prosrc, prosecdef, proconfig FROM pg_proc "
                            "WHERE oid = CAST(:sig AS regprocedure)"
                        ),
                        {"sig": _INSERT_SPACE},
                    )
                ).one()
                assert "IS NOT TRUE" in insert_src
                assert "accepted_at" in insert_src
                assert "42501" in insert_src
                assert insert_secdef is True
                assert "search_path=public" in (insert_config or [])

                member_src, member_secdef = (
                    await conn.execute(
                        text(
                            "SELECT prosrc, prosecdef FROM pg_proc "
                            "WHERE oid = CAST(:sig AS regprocedure)"
                        ),
                        {"sig": _MEMBER_CHECK},
                    )
                ).one()
                assert "accepted_at IS NOT NULL" in member_src
                assert member_secdef is True

                current_user = await conn.scalar(text("SELECT current_user"))
                app_user = get_settings().postgres_app_user.strip() or "memchentic_app"
                assert current_user == app_user
                for signature in (_INSERT_SPACE, _MEMBER_CHECK):
                    public = await conn.scalar(
                        text("SELECT has_function_privilege('public', :sig, 'EXECUTE')"),
                        {"sig": signature},
                    )
                    assert public is False, signature
                    app_role = await conn.scalar(
                        text("SELECT has_function_privilege(current_user, :sig, 'EXECUTE')"),
                        {"sig": signature},
                    )
                    assert app_role is True, signature
        finally:
            await engine.dispose()

    asyncio.run(run())
