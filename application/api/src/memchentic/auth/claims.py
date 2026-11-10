"""Group-name extraction from IdP token claims."""

from __future__ import annotations

from typing import Any


def groups_from_claims(claims: dict[str, Any]) -> set[str]:
    """Extract IdP group ids/names from ID token / userinfo claims.

    Args:
        claims: Merged OIDC claims.

    Returns:
        Set of string ids and names suitable for bind matching.
    """
    out: set[str] = set()
    raw = claims.get("groups")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item.strip():
                out.add(item.strip())
            elif isinstance(item, dict):
                for key in ("id", "groupId", "value", "name"):
                    val = item.get(key)
                    if isinstance(val, str) and val.strip():
                        out.add(val.strip())
    elif isinstance(raw, str) and raw.strip():
        out.add(raw.strip())

    realm = claims.get("realm_access")
    if isinstance(realm, dict):
        roles = realm.get("roles")
        if isinstance(roles, list):
            for role in roles:
                if isinstance(role, str) and role.strip():
                    out.add(role.strip())

    return out
