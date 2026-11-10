"""RLS context and pooled connections: plain commits clear it, commit_with_rls keeps it."""

from __future__ import annotations

import uuid

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.principal import Principal
from memchentic.db.session import (
    _reset_rls_on_checkin,
    commit_with_rls,
    get_engine,
    set_rls_context,
)
from memchentic.services.spaces import create_group, list_spaces, rename_space
from memchentic.services.users import register_internal_user

_GUCS = text("SELECT current_setting('app.user_id', true), current_setting('app.is_admin', true)")


async def _gucs(session: AsyncSession) -> tuple[str | None, str | None]:
    row = (await session.execute(_GUCS)).one()
    return row[0], row[1]


def test_runtime_engine_registers_checkin_reset() -> None:
    assert event.contains(get_engine().sync_engine, "checkin", _reset_rls_on_checkin)


async def test_plain_commit_clears_rls_context(pooled_session: AsyncSession) -> None:
    await set_rls_context(pooled_session, user_id=None, is_admin=True)
    assert (await _gucs(pooled_session))[1] == "true"

    await pooled_session.commit()

    user_id, is_admin = await _gucs(pooled_session)
    assert not user_id
    assert is_admin != "true"


async def test_commit_with_rls_keeps_context(pooled_session: AsyncSession) -> None:
    user_id = uuid.uuid4()
    await set_rls_context(pooled_session, user_id=user_id, is_admin=False)

    await commit_with_rls(pooled_session, user_id=user_id, is_admin=False)

    assert await _gucs(pooled_session) == (str(user_id), "false")


async def test_space_services_keep_context_across_their_commits(
    pooled_session: AsyncSession,
) -> None:
    await set_rls_context(pooled_session, user_id=None, is_admin=True)
    user = await register_internal_user(
        pooled_session,
        email=f"pool-{uuid.uuid4().hex[:10]}@example.com",
        password="password-ok-12",
    )
    principal = Principal(is_admin=False, user_id=user.id, email=user.email)
    # register_internal_user ends with a plain commit: bind the caller again,
    # as get_principal does at the start of a request.
    await set_rls_context(pooled_session, user_id=user.id, is_admin=False)

    group = await create_group(pooled_session, principal, name=f"pool-{uuid.uuid4().hex[:8]}")
    new_name = f"pool-renamed-{uuid.uuid4().hex[:8]}"
    renamed = await rename_space(pooled_session, principal, group.id, new_name)
    names = {space.name for space in await list_spaces(pooled_session, principal)}

    assert renamed.name == new_name
    assert new_name in names
