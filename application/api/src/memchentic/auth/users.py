from __future__ import annotations

import uuid
from collections.abc import Collection
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from memchentic.auth.passwords import hash_password, verify_password
from memchentic.db.session import set_rls_context
from memchentic.models.entities import Space, SpaceKind, SpaceMember, User, UserIdentity
from memchentic.services.spaces import ensure_personal_space

if TYPE_CHECKING:
    from memchentic.auth.principal import Principal
    from memchentic.settings import Settings

# Fixed bcrypt hash so missing-user auth still pays verify cost (timing pad).
_MISSING_USER_DUMMY_HASH = (
    "$2b$12$ZXqUKsbftiNtBvL4M9MFUeYf3mnv8n4FqIquSwBfi0224L2Mg55/m"
)


def normalize_email(email: str) -> str:
    """Normalize an address used as the stable user identity.

    Args:
        email: Raw email from registration, admin create, or an IdP claim.

    Returns:
        Casefolded, trimmed email (ASCII-safe lowercase for typical addresses).

    Raises:
        ValueError: If the value is empty after trimming.
    """
    email_n = (email or "").strip().casefold()
    if not email_n:
        raise ValueError("Email is required")
    return email_n


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    """Load a user by canonical email (case-insensitive).

    Args:
        session: Database session.
        email: Email to look up; normalized before query.

    Returns:
        The matching user, or None.
    """
    email_n = normalize_email(email)
    result = await session.execute(select(User).where(func.lower(User.email) == email_n))
    return result.scalar_one_or_none()


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    """Load a user by id with login bindings.

    Args:
        session: Database session.
        user_id: User primary key.

    Returns:
        The matching user, or None.
    """
    result = await session.execute(
        select(User).options(selectinload(User.identities)).where(User.id == user_id)
    )
    return result.scalar_one_or_none()


async def get_user_by_provider_subject(
    session: AsyncSession, *, provider: str, subject: str
) -> User | None:
    """Load a user by a durable provider binding.

    Args:
        session: Database session.
        provider: OAuth / internal provider name.
        subject: Provider-stable subject.

    Returns:
        The matching user, or None.
    """
    result = await session.execute(
        select(User)
        .join(UserIdentity, UserIdentity.user_id == User.id)
        .options(selectinload(User.identities))
        .where(UserIdentity.provider == provider, UserIdentity.provider_subject == subject)
    )
    return result.scalar_one_or_none()


def providers_for(user: User) -> list[str]:
    """Return sorted provider names linked to ``user``.

    Args:
        user: User that may have ``identities`` loaded.

    Returns:
        Distinct provider names, preferring loaded identities and falling back
        to the denormalized last-used provider.
    """
    if user.identities:
        return sorted({i.provider for i in user.identities})
    return [user.provider] if user.provider else []


async def _ensure_identity(
    session: AsyncSession,
    user: User,
    *,
    provider: str,
    subject: str,
) -> None:
    """Attach or refresh a provider binding without dropping other IdPs.

    Args:
        session: Database session.
        user: Email-keyed user row.
        provider: Provider name to bind.
        subject: Provider-stable subject.

    Raises:
        ValueError: If the binding already belongs to a different user.
    """
    existing = await session.execute(
        select(UserIdentity).where(
            UserIdentity.provider == provider, UserIdentity.provider_subject == subject
        )
    )
    row = existing.scalar_one_or_none()
    if row and row.user_id != user.id:
        raise ValueError("Provider binding already belongs to another user")
    if row:
        return

    same_provider = await session.execute(
        select(UserIdentity).where(
            UserIdentity.user_id == user.id, UserIdentity.provider == provider
        )
    )
    current = same_provider.scalar_one_or_none()
    if current:
        current.provider_subject = subject
        return

    session.add(
        UserIdentity(
            id=uuid.uuid4(),
            user_id=user.id,
            provider=provider,
            provider_subject=subject,
        )
    )


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
    user = User(
        id=uuid.uuid4(),
        email=email_n,
        password_hash=hash_password(password),
        provider="internal",
        provider_subject=email_n,
        display_name=display_name or email_n.split("@")[0],
    )
    session.add(user)
    await session.flush()
    # App-role RLS: space insert/membership checks need this principal set.
    await set_rls_context(session, user_id=user.id, is_admin=False)
    await _ensure_identity(session, user, provider="internal", subject=email_n)
    await ensure_personal_space(session, user)
    from memchentic.services.auth_groups import add_to_users_group

    await add_to_users_group(session, user.id)
    await session.commit()
    await session.refresh(user)
    return user


async def authenticate_internal(
    session: AsyncSession, *, email: str, password: str
) -> User | None:
    """Authenticate an internal user by email and password.

    Args:
        session: Database session.
        email: Login email.
        password: Plaintext password.

    Returns:
        The user when credentials match, otherwise None.
    """
    try:
        email_n = normalize_email(email)
    except ValueError:
        verify_password(password, _MISSING_USER_DUMMY_HASH)
        return None
    user = await get_user_by_email(session, email_n)
    if not user or not user.password_hash:
        verify_password(password, _MISSING_USER_DUMMY_HASH)
        return None
    if not verify_password(password, user.password_hash):
        return None
    await _ensure_identity(session, user, provider="internal", subject=email_n)
    user.provider = "internal"
    user.provider_subject = email_n
    await session.commit()
    await session.refresh(user)
    return user


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
    verified email keeps prior logins working. When ``idp_groups`` is provided,
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
        ValueError: If email is missing/unverified, or a provider binding
            conflict would orphan another account.
    """
    if not email_verified:
        raise ValueError("OAuth email must be verified")
    email_n = normalize_email(email)
    subject_n = subject.strip()
    if not subject_n:
        raise ValueError("OAuth subject is required")
    groups = list(idp_groups or [])

    async def _finish(user: User) -> User:
        from memchentic.services.auth_groups import sync_user_from_idp_groups

        await sync_user_from_idp_groups(session, user, groups, commit=False)
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
        await _ensure_identity(session, by_email, provider=provider, subject=subject_n)
        by_email.provider = provider
        by_email.provider_subject = subject_n
        if display_name:
            by_email.display_name = display_name
        return await _finish(by_email)

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
    await _ensure_identity(session, user, provider=provider, subject=subject_n)
    await ensure_personal_space(session, user)
    from memchentic.services.auth_groups import add_to_users_group

    await add_to_users_group(session, user.id)
    return await _finish(user)


async def ensure_admin_user(
    session: AsyncSession,
    settings: Settings | None = None,
) -> User:
    """Ensure the break-glass admin exists as a real ``User`` row.

    ``ADMIN_EMAIL`` / ``ADMIN_PASSWORD`` / ``ADMIN_DISPLAY_NAME`` stay the
    source of truth. Creates the user when missing, syncs password + display
    name when present, and ensures a personal space.

    Args:
        session: Database session.
        settings: Settings override; defaults to the cached settings.

    Returns:
        The admin user row.
    """
    from memchentic.settings import get_settings

    settings = settings or get_settings()
    email_n = normalize_email(settings.admin_contact_email)
    display = settings.admin_display_name.strip() or email_n.split("@")[0]

    user = await get_user_by_email(session, email_n)
    if user is None:
        user = User(
            id=uuid.uuid4(),
            email=email_n,
            password_hash=hash_password(settings.admin_password),
            provider="internal",
            provider_subject=email_n,
            display_name=display,
        )
        session.add(user)
        await session.flush()
    else:
        if not user.password_hash or not verify_password(
            settings.admin_password, user.password_hash
        ):
            user.password_hash = hash_password(settings.admin_password)
        if user.display_name != display:
            user.display_name = display
    await _ensure_identity(session, user, provider="internal", subject=email_n)
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
        select(User)
        .options(selectinload(User.identities))
        .order_by(User.created_at.desc())
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
    if not principal.user_id:
        return False
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
            caller_m.user_id == principal.user_id,
            Space.kind == SpaceKind.group,
            Space.archived_at.is_(None),
        )
        .limit(1)
    )
    return hit.scalar_one_or_none() is not None


async def list_directory_users(
    session: AsyncSession, principal: Principal
) -> list[User]:
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
    if not principal.user_id:
        return []
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
            Space.kind == SpaceKind.group,
            Space.archived_at.is_(None),
        )
        .order_by(User.created_at.desc())
    )
    return list(result.scalars().unique())


async def lookup_user_by_email(
    session: AsyncSession, principal: Principal, email: str
) -> User | None:
    """Exact email lookup for roster managers (and admins).

    Returns None for a miss and for callers who are not admins and do not
    manage any group — callers should map both to the same 404.

    Args:
        session: Database session.
        principal: Authenticated caller.
        email: Address to match (casefolded via ``get_user_by_email``).

    Returns:
        The matching user, or None when not found / not authorized.
    """
    try:
        user = await get_user_by_email(session, email)
    except ValueError:
        return None
    if not user:
        return None
    if principal.is_admin:
        return user
    if not principal.user_id:
        return None
    managed = await session.execute(
        select(Space.id)
        .where(
            Space.kind == SpaceKind.group,
            Space.archived_at.is_(None),
            Space.created_by_user_id == principal.user_id,
        )
        .limit(1)
    )
    if managed.scalar_one_or_none() is None:
        return None
    return user


async def user_delete_eligibility(
    session: AsyncSession,
    *,
    actor_user_id: uuid.UUID | None,
    target: User,
    settings: Settings | None = None,
) -> tuple[bool, str | None]:
    """Whether an admin may hard-delete ``target``.

    Args:
        session: Database session.
        actor_user_id: Calling admin's user id (self-delete check).
        target: Directory user under consideration.
        settings: Optional settings override.

    Returns:
        ``(deletable, reason)`` — reason is set when not deletable.
    """
    from memchentic.services.auth_groups import user_in_idp_bound_roster
    from memchentic.settings import get_settings

    settings = settings or get_settings()
    if normalize_email(target.email) == normalize_email(settings.admin_contact_email):
        return False, "Admin account cannot be deleted"
    if actor_user_id and target.id == actor_user_id:
        return False, "Cannot delete your own account"
    if await user_in_idp_bound_roster(session, target.id):
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
    from memchentic.services.spaces import get_personal_space
    from memchentic.settings import get_settings

    settings = settings or get_settings()
    user = await get_user(session, target_user_id)
    if not user:
        raise LookupError("User not found")
    ok, reason = await user_delete_eligibility(
        session, actor_user_id=actor_user_id, target=user, settings=settings
    )
    if not ok:
        raise PermissionError(reason or "Cannot delete user")

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
