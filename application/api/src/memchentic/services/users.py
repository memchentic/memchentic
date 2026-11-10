"""User lifecycle: registration, IdP upsert, admin bootstrap, directory, deletion."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from memchentic.auth.passwords import hash_password, password_needs_rehash, verify_password
from memchentic.auth.principal import Principal
from memchentic.auth.users import (
    ensure_identity,
    get_user,
    get_user_by_email,
    get_user_by_provider_subject,
    normalize_email,
    revoke_credentials_raw,
)
from memchentic.db.session import commit_with_rls, set_rls_context
from memchentic.models.entities import Space, SpaceKind, SpaceMember, User, UserIdentity
from memchentic.services.auth_roster import (
    add_to_users_group,
    idp_signup_allowed,
    sync_user_from_idp_groups,
    user_in_idp_bound_roster,
)
from memchentic.services.errors import ForbiddenError, NotFoundError
from memchentic.services.spaces import ensure_personal_space, get_personal_space
from memchentic.settings import Settings, get_settings

logger = logging.getLogger("memchentic")


async def register_internal_user(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    display_name: str | None = None,
) -> User:
    """Create an internal (email/password) user.

    Args:
        session: Database session.
        email: Required unique identity for the user.
        password: Plaintext password to hash.
        display_name: Optional display name; defaults to the local-part of email.

    Returns:
        The persisted user with a personal space.

    Raises:
        ValueError: If email is missing or already registered.
    """
    email_n = normalize_email(email)
    existing = await get_user_by_email(session, email_n)
    if existing:
        raise ValueError("Email already registered")
    # bcrypt is CPU-bound: run it off the event loop.
    password_hash = await asyncio.to_thread(hash_password, password)
    user = User(
        id=uuid.uuid4(),
        email=email_n,
        password_hash=password_hash,
        provider="internal",
        provider_subject=email_n,
        display_name=display_name or email_n.split("@")[0],
    )
    try:
        # Savepoint: an overlapping registration of the same email loses on the
        # unique index here. Rolling back only the savepoint keeps the
        # transaction, and the RLS settings bound to it, usable.
        async with session.begin_nested():
            session.add(user)
            await session.flush()
    except IntegrityError as exc:
        raise ValueError("Email already registered") from exc
    # App-role RLS: space insert/membership checks need this principal set.
    await set_rls_context(session, user_id=user.id, is_admin=False)
    await ensure_identity(session, user, provider="internal", subject=email_n)
    await ensure_personal_space(session, user)

    await add_to_users_group(session, user.id)
    await session.commit()
    await session.refresh(user)
    return user


async def _revoke_unverified_credentials(session: AsyncSession, user: User) -> None:
    """Revoke every credential created before an IdP proved ownership of the email.

    Internal registration does not verify the email address. When an external
    IdP first vouches for the address of a password-bearing account, the
    password, the ``internal`` identity, GUI sessions, refresh tokens, pending
    authorization codes and API keys may belong to somebody else. The caller
    commits.

    Args:
        session: Database session.
        user: The account being linked to an IdP identity.
    """
    user.password_hash = None
    internal = (
        await session.execute(
            select(UserIdentity).where(
                UserIdentity.user_id == user.id, UserIdentity.provider == "internal"
            )
        )
    ).scalar_one_or_none()
    if internal is not None:
        await session.delete(internal)
    # api_keys is under row-level security; bind the owner so the UPDATE sees the rows.
    await set_rls_context(session, user_id=user.id, is_admin=False)
    await revoke_credentials_raw(session, user.id)
    logger.warning(
        "First IdP link for user %s: revoked password, sessions, tokens and API keys",
        user.id,
    )


async def upsert_oauth_user(
    session: AsyncSession,
    *,
    provider: str,
    subject: str,
    email: str,
    display_name: str | None = None,
    email_verified: bool = True,
    idp_groups: Collection[str] | None = None,
) -> User:
    """Find or create a user keyed by email; accumulate OAuth provider bindings.

    Email is the stable identity. Each successful IdP login adds (or refreshes)
    that provider's subject on the same user without removing other bindings,
    so switching ``OAUTH_PROVIDER`` or linking another IdP with the same
    verified email keeps prior logins working. The first IdP link to an
    account that still has a password revokes that password and every session,
    token and API key issued before the link, because internal registration
    does not prove ownership of the email. When ``idp_groups`` is provided,
    Auth Group membership is synced from IdP binds.

    Args:
        session: Database session.
        provider: Active OAuth provider name (e.g. ``google``, ``github``).
        subject: Provider-stable subject / user id claim.
        email: Required verified email from the IdP.
        display_name: Optional display name from the IdP profile.
        email_verified: Whether the IdP asserts the email is verified.
        idp_groups: Optional IdP group ids/names from the login claims.

    Returns:
        The existing or newly created user.

    Raises:
        ValueError: If email is missing/unverified, a provider binding conflict
            would orphan another account, or a new account is not admitted
            (registration disabled, or the person is outside a bound auth group).
    """
    if not email_verified:
        raise ValueError("OAuth email must be verified")
    email_n = normalize_email(email)
    subject_n = subject.strip()
    if not subject_n:
        raise ValueError("OAuth subject is required")
    groups = list(idp_groups or [])

    async def _finish(user: User) -> User:

        await sync_user_from_idp_groups(session, user, groups)
        await session.commit()
        await session.refresh(user)
        return user

    user = await get_user_by_provider_subject(session, provider=provider, subject=subject_n)
    if user:
        if user.email != email_n:
            clash = await get_user_by_email(session, email_n)
            if clash and clash.id != user.id:
                raise ValueError("Email already belongs to another user")
            user.email = email_n
        if display_name:
            user.display_name = display_name
        user.provider = provider
        user.provider_subject = subject_n
        return await _finish(user)

    by_email = await get_user_by_email(session, email_n)
    if by_email:
        is_env_admin = email_n == normalize_email(get_settings().admin_contact_email)
        if by_email.password_hash is not None and not is_env_admin:
            await _revoke_unverified_credentials(session, by_email)
        await ensure_identity(session, by_email, provider=provider, subject=subject_n)
        by_email.provider = provider
        by_email.provider_subject = subject_n
        if display_name:
            by_email.display_name = display_name
        return await _finish(by_email)

    if not await idp_signup_allowed(session, groups):
        raise ValueError("Sign-up is disabled for this instance; ask an administrator for access")

    user = User(
        id=uuid.uuid4(),
        email=email_n,
        password_hash=None,
        provider=provider,
        provider_subject=subject_n,
        display_name=display_name or email_n.split("@")[0],
    )
    session.add(user)
    await session.flush()
    await set_rls_context(session, user_id=user.id, is_admin=False)
    await ensure_identity(session, user, provider=provider, subject=subject_n)
    await ensure_personal_space(session, user)

    await add_to_users_group(session, user.id)
    return await _finish(user)


async def ensure_admin_user(
    session: AsyncSession,
    settings: Settings | None = None,
) -> User:
    """Ensure the ``ADMIN_EMAIL`` administrator exists as a real ``User`` row.

    ``ADMIN_EMAIL`` / ``ADMIN_PASSWORD`` / ``ADMIN_DISPLAY_NAME`` stay the
    source of truth. Creates the user when missing, syncs password + display
    name when present, and ensures a personal space.

    Args:
        session: Database session.
        settings: Settings override; defaults to the cached settings.

    Returns:
        The admin user row.
    """
    settings = settings or get_settings()
    email_n = normalize_email(settings.admin_contact_email)
    display = settings.admin_display_name.strip() or email_n.split("@")[0]

    user = await get_user_by_email(session, email_n)
    if user is None:
        user = User(
            id=uuid.uuid4(),
            email=email_n,
            password_hash=await asyncio.to_thread(hash_password, settings.admin_password),
            provider="internal",
            provider_subject=email_n,
            display_name=display,
        )
        session.add(user)
        await session.flush()
    else:
        if (
            not user.password_hash
            or password_needs_rehash(user.password_hash)
            or not await asyncio.to_thread(
                verify_password, settings.admin_password, user.password_hash
            )
        ):
            user.password_hash = await asyncio.to_thread(hash_password, settings.admin_password)
        if user.display_name != display:
            user.display_name = display
    await ensure_identity(session, user, provider="internal", subject=email_n)
    await ensure_personal_space(session, user)
    await session.flush()
    return user


async def list_users(session: AsyncSession) -> list[User]:
    """List users newest-first for the directory.

    Args:
        session: Database session.

    Returns:
        All users ordered by created_at descending, with identities loaded.
    """
    result = await session.execute(
        select(User).options(selectinload(User.identities)).order_by(User.created_at.desc())
    )
    return list(result.scalars())


async def directory_user_visible(
    session: AsyncSession, principal: Principal, user_id: uuid.UUID
) -> bool:
    """True when the caller may see this user's directory detail.

    Admins and self always pass. Other callers need a shared unarchived group.
    """
    if principal.is_admin:
        return True
    if principal.user_id == user_id:
        return True
    caller_m = aliased(SpaceMember)
    other_m = aliased(SpaceMember)
    hit = await session.execute(
        select(Space.id)
        .join(other_m, other_m.space_id == Space.id)
        .join(caller_m, caller_m.space_id == Space.id)
        .where(
            other_m.user_id == user_id,
            other_m.accepted_at.is_not(None),
            caller_m.user_id == principal.user_id,
            caller_m.accepted_at.is_not(None),
            Space.kind == SpaceKind.group,
            Space.archived_at.is_(None),
        )
        .limit(1)
    )
    return hit.scalar_one_or_none() is not None


async def list_directory_users(session: AsyncSession, principal: Principal) -> list[User]:
    """List users visible in the authenticated directory.

    Admins see everyone. Other callers only see users who share at least one
    unarchived group space (not personal) with them.

    Args:
        session: Database session.
        principal: Authenticated caller.

    Returns:
        Users ordered by created_at descending, with identities loaded.
    """
    if principal.is_admin:
        return await list_users(session)
    caller_m = aliased(SpaceMember)
    other_m = aliased(SpaceMember)
    result = await session.execute(
        select(User)
        .options(selectinload(User.identities))
        .join(other_m, other_m.user_id == User.id)
        .join(Space, Space.id == other_m.space_id)
        .join(caller_m, caller_m.space_id == Space.id)
        .where(
            caller_m.user_id == principal.user_id,
            caller_m.accepted_at.is_not(None),
            other_m.accepted_at.is_not(None),
            Space.kind == SpaceKind.group,
            Space.archived_at.is_(None),
        )
        .order_by(User.created_at.desc())
    )
    return list(result.scalars().unique())


async def lookup_user_by_email(
    session: AsyncSession, principal: Principal, email: str
) -> User | None:
    """Exact email lookup, for admins only.

    Returns None for a miss and for every non-admin caller. The route maps
    both to the same 404, so the lookup cannot be used to test whether an
    address is registered.

    Args:
        session: Database session.
        principal: Authenticated caller.
        email: Address to match (casefolded via ``get_user_by_email``).

    Returns:
        The matching user, or None when not found / not authorized.
    """
    if not principal.is_admin:
        return None
    try:
        return await get_user_by_email(session, email)
    except ValueError:
        return None


async def user_delete_eligibility(
    session: AsyncSession,
    *,
    actor_user_id: uuid.UUID | None,
    target: User,
    settings: Settings | None = None,
    bound_user_ids: Collection[uuid.UUID] | None = None,
) -> tuple[bool, str | None]:
    """Whether an admin may hard-delete ``target``.

    Args:
        session: Database session.
        actor_user_id: Calling admin's user id (self-delete check).
        target: Directory user under consideration.
        settings: Optional settings override.
        bound_user_ids: Optional precomputed ids of users on an IdP-bound
            auth group roster. When given, no roster query runs.

    Returns:
        ``(deletable, reason)`` — reason is set when not deletable.
    """
    settings = settings or get_settings()
    if normalize_email(target.email) == normalize_email(settings.admin_contact_email):
        return False, "Admin account cannot be deleted"
    if actor_user_id and target.id == actor_user_id:
        return False, "Cannot delete your own account"
    if bound_user_ids is not None:
        on_bound_roster = target.id in bound_user_ids
    else:
        on_bound_roster = await user_in_idp_bound_roster(session, target.id)
    if on_bound_roster:
        return False, "User is managed by an IdP-bound auth group"
    return True, None


async def delete_user(
    session: AsyncSession,
    *,
    actor_user_id: uuid.UUID | None,
    target_user_id: uuid.UUID,
    settings: Settings | None = None,
) -> None:
    """Hard-delete a directory user (admin). Removes their personal space first.

    Args:
        session: Database session.
        actor_user_id: Calling admin's user id.
        target_user_id: User to remove.
        settings: Optional settings override.

    Raises:
        LookupError: Target user missing.
        PermissionError: Env admin, self-delete, or IdP-bound roster member.
    """
    settings = settings or get_settings()
    user = await get_user(session, target_user_id)
    if not user:
        raise NotFoundError("User not found")
    ok, reason = await user_delete_eligibility(
        session, actor_user_id=actor_user_id, target=user, settings=settings
    )
    if not ok:
        raise ForbiddenError(reason or "Cannot delete user")

    # Reassign groups this user owns so remaining members keep a manager.
    if actor_user_id is not None:
        owned = list(
            (
                await session.execute(
                    select(Space).where(
                        Space.kind == SpaceKind.group,
                        Space.created_by_user_id == user.id,
                    )
                )
            ).scalars()
        )
        for space in owned:
            space.created_by_user_id = actor_user_id

    personal = await get_personal_space(session, user.id)
    if personal:
        await session.delete(personal)
        await session.flush()
    await session.delete(user)
    await session.commit()


async def revoke_user_credentials(
    session: AsyncSession, principal: Principal, user_id: uuid.UUID
) -> None:
    """Admin action: end all sessions, refresh tokens and API keys of a user.

    The account, its password, its roster rows and its memories are kept.
    Access tokens that were already issued stay valid until they expire
    (``ACCESS_TOKEN_SECONDS``); they cannot be refreshed.

    Args:
        session: Database session.
        principal: Authenticated caller; must be an administrator.
        user_id: Target account.

    Raises:
        ForbiddenError: The caller is not an administrator.
        NotFoundError: The target user does not exist.
    """
    if not principal.is_admin:
        raise ForbiddenError("Admin required")
    if await get_user(session, user_id) is None:
        raise NotFoundError("User not found")
    await revoke_credentials_raw(session, user_id)
    await commit_with_rls(session, user_id=principal.user_id, is_admin=principal.is_admin)
