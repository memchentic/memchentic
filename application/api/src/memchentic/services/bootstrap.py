"""Startup bootstrap: admin user, auth groups, instance settings, backfill, signing key."""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from memchentic.auth.tokens import verify_signing_key
from memchentic.db.session import set_rls_context
from memchentic.services.auth_roster import ensure_auth_groups
from memchentic.services.instance_settings import ensure_instance_settings
from memchentic.services.memories import backfill_orphan_creators
from memchentic.services.users import ensure_admin_user
from memchentic.settings import Settings

logger = logging.getLogger("memchentic")


async def bootstrap_instance(session: AsyncSession, settings: Settings) -> None:
    """Bring a migrated database to a servable state.

    Ensures the ``ADMIN_EMAIL`` user, the auth groups and the instance
    settings, attributes orphan memories to their space owner, and checks
    the signing key when the internal provider is active.

    Args:
        session: Database session (the caller opens and closes it).
        settings: Application settings.
    """
    await set_rls_context(session, user_id=None, is_admin=True)
    admin = await ensure_admin_user(session, settings)
    await ensure_auth_groups(session, admin)
    await ensure_instance_settings(session, settings)
    # Attribute orphan memories to the space owner only (never silently
    # claim them as the env admin — that hides deleted-owner history).
    # The helper binds its own admin RLS context: the calls above rebind
    # and commit, and every commit clears the context.
    backfilled = await backfill_orphan_creators(session)
    if backfilled:
        logger.info("Backfilled created_by on %s memories to space owner", backfilled)
    if settings.oauth_provider == "internal":
        # Refuse to start when ENCRYPTION_KEY cannot decrypt the stored
        # signing key; otherwise every sign-in fails at request time.
        await verify_signing_key(session, settings)
